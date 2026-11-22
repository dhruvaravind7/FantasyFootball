"""Trades: proposing, the bot rule, the deadline, roster fits, review, locks, vetoes, and conflicts."""
from datetime import datetime, timedelta

import pytest
from rest_framework.exceptions import ValidationError

from fantasy.models import RosterEntry, Trade, WaiverClaim
from fantasy.trades import accept_trade, process_due_trades, process_trade, propose_trade

from .conftest import EASTERN, KICKOFF, NOW, WEEK


def bench(team, position: str) -> list[RosterEntry]:
    """The team's bench rows at one position this week, oldest first."""
    return list(team.roster.filter(week=WEEK, slot=RosterEntry.Slot.BENCH, player__position=position).order_by("id"))


@pytest.fixture
def offer(league):
    """Me offers my first bench QB for You's bench QB (a 1-for-1 that fits both rosters)."""
    mine, theirs = bench(league.me, "QB")[0], bench(league.you, "QB")[0]
    trade = propose_trade(league.me, league.you, [mine.id], [theirs.id], [])
    return trade, mine, theirs


def test_bots_decline_at_once(league):
    """A bot never trades: the offer is DECLINED the moment it's made."""
    bot = league.bots[0]
    trade = propose_trade(league.me, bot, [bench(league.me, "QB")[0].id], [bench(bot, "QB")[0].id], [])
    assert trade.status == Trade.Status.DECLINED


def test_past_the_deadline(league):
    """Week 9 is past a week-8 deadline."""
    league.league.trade_deadline = 8
    league.league.save()
    with pytest.raises(ValidationError, match="deadline"):
        propose_trade(league.me, league.you, [bench(league.me, "QB")[0].id], [bench(league.you, "QB")[0].id], [])


def test_two_for_one_needs_a_drop(league):
    """Getting 2 players for 1 on a full roster is one too many, unless I drop someone too."""
    mine = bench(league.me, "QB")[0]
    two = [bench(league.you, "WR")[0].id, bench(league.you, "TE")[0].id]
    with pytest.raises(ValidationError, match="Add a drop"):
        propose_trade(league.me, league.you, [mine.id], two, [])
    drop = bench(league.me, "RB")[0]
    assert propose_trade(league.me, league.you, [mine.id], two, [drop.id]).status == Trade.Status.PROPOSED


def test_full_trade_through_the_api(league, api, clock):
    """Propose, accept, wait out the 2-day review, and the two players switch teams onto the bench."""
    mine, theirs = bench(league.me, "QB")[0], bench(league.you, "QB")[0]
    response = api(league.me_user).post(f"/api/fantasy/teams/{league.me.id}/trades/", {
        "receiver_id": league.you.id, "send_entry_ids": [mine.id], "receive_entry_ids": [theirs.id]}, format="json")
    assert response.status_code == 200
    trade_id = response.data[0]["id"]

    response = api(league.you_user).post(f"/api/fantasy/trades/{trade_id}/accept/", {}, format="json")
    assert response.status_code == 200
    assert response.data["status"] == Trade.Status.ACCEPTED

    assert process_due_trades() == 0                                        # still in review
    clock.move_to(NOW + timedelta(days=2, minutes=1))                       # Friday 12:01, review over, before kickoff
    assert process_due_trades() == 1

    mine.refresh_from_db()
    theirs.refresh_from_db()
    assert (mine.team_id, mine.slot) == (league.you.id, RosterEntry.Slot.BENCH)
    assert (theirs.team_id, theirs.slot) == (league.me.id, RosterEntry.Slot.BENCH)
    public = api(league.you_user).get(f"/api/fantasy/leagues/{league.league.id}/trades/")
    assert [t["status"] for t in public.data] == [Trade.Status.COMPLETED]


def test_waits_while_a_player_is_locked(league, offer, clock):
    """Once Sunday's games kick off, nothing moves: the trade stays ACCEPTED for a later pass."""
    trade, mine, _ = offer
    accept_trade(trade, [])
    clock.move_to(KICKOFF + timedelta(hours=1))                             # review over, but every game has started
    assert process_trade(trade) is False
    trade.refresh_from_db()
    mine.refresh_from_db()
    assert trade.status == Trade.Status.ACCEPTED
    assert mine.team_id == league.me.id


def test_only_the_commissioner_vetoes(league, offer, api):
    """You (not the commissioner) get a 403; Me (the commissioner) vetoes, and the trade never processes."""
    trade, _, _ = offer
    accept_trade(trade, [])
    assert api(league.you_user).post(f"/api/fantasy/trades/{trade.id}/veto/").status_code == 403
    assert api(league.me_user).post(f"/api/fantasy/trades/{trade.id}/veto/").status_code == 200
    trade.refresh_from_db()
    assert trade.status == Trade.Status.VETOED
    assert process_due_trades(NOW + timedelta(days=3)) == 0


def test_completing_fails_conflicts(league, offer, clock):
    """After the trade completes, another offer with the same player, and a claim that drops him, both FAIL."""
    trade, mine, theirs = offer
    other = propose_trade(league.me, league.you, [mine.id], [bench(league.you, "WR")[0].id], [])
    claim = WaiverClaim.objects.create(team=league.you, player=bench(league.bots[0], "WR")[0].player,
                                       drop_player=theirs.player, order=1)   # You plans to drop the QB they're trading
    accept_trade(trade, [])
    clock.move_to(datetime(2026, 11, 13, 12, 1, tzinfo=EASTERN))
    assert process_trade(trade) is True

    other.refresh_from_db()
    claim.refresh_from_db()
    assert other.status == Trade.Status.FAILED
    assert claim.status == WaiverClaim.Status.FAILED
