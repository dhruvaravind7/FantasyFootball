"""Bot roster moves (13c): out-for-season drops, IR stashes with a pickup, coming back off IR, and open spots."""
from datetime import date

import pytest

from fantasy.bots import bot_moves
from fantasy.models import RosterEntry, WaiverHold
from league.models import Player

from .conftest import WEEK

SEASON_OVER = date(2027, 2, 1)       # after fantasy_season_end (week 1's kickoff + 17 weeks = Jan 10, 2027)
BACK_SOON = date(2026, 11, 29)       # a 2-week injury


@pytest.fixture
def bot(league):
    """The first bot, plus a starter of his at RB (the position with the most free agents)."""
    team = league.bots[0]
    starter = team.roster.filter(week=WEEK, slot=RosterEntry.Slot.RB).order_by("id").first()
    return team, starter


def hurt(entry: RosterEntry, status: str, back: date | None) -> None:
    """Give this row's player an injury status and a return date."""
    Player.objects.filter(pk=entry.player_id).update(injury_status=status, return_date=back)


def active_count(team) -> int:
    """Rows this week that count toward the roster limit (everything but IR)."""
    return team.roster.filter(week=WEEK).exclude(slot=RosterEntry.Slot.IR).count()


def test_out_for_the_season_is_dropped(league, bot):
    """He's dropped onto waivers, and an RB takes his spot."""
    team, starter = bot
    rbs_before = team.roster.filter(week=WEEK, player__position="RB").count()
    hurt(starter, Player.InjuryStatus.INJURED_RESERVE, SEASON_OVER)
    assert bot_moves(league.league) >= 1
    assert not team.roster.filter(week=WEEK, player=starter.player).exists()
    assert WaiverHold.objects.filter(league=league.league, player=starter.player).exists()
    assert team.roster.filter(week=WEEK, player__position="RB").count() == rbs_before
    assert active_count(team) == league.league.roster_size


def test_short_injury_goes_to_ir_with_a_pickup(league, bot):
    """A 2-week injury is stashed on IR (not dropped), and the open spot is filled at his position."""
    team, starter = bot
    hurt(starter, Player.InjuryStatus.OUT, BACK_SOON)
    bot_moves(league.league)
    starter.refresh_from_db()
    assert starter.slot == RosterEntry.Slot.IR
    assert active_count(team) == league.league.roster_size


def test_healthy_again_comes_off_ir(league, bot):
    """When he's healthy, he leaves IR and someone (the worst of bench + him) is dropped to make room."""
    team, starter = bot
    hurt(starter, Player.InjuryStatus.OUT, BACK_SOON)
    bot_moves(league.league)
    assert team.roster.filter(week=WEEK, slot=RosterEntry.Slot.IR, player=starter.player).exists()
    hurt(starter, "", None)                                   # healthy again
    bot_moves(league.league)
    assert not team.roster.filter(week=WEEK, slot=RosterEntry.Slot.IR).exists()
    assert active_count(team) == league.league.roster_size


def test_open_spot_is_filled_but_never_a_second_kicker(league):
    """A bot with an empty spot picks up the best available player, and it isn't a second K."""
    team = league.bots[1]
    team.roster.filter(week=WEEK, slot=RosterEntry.Slot.BENCH).order_by("id").first().delete()
    assert bot_moves(league.league) == 1
    assert active_count(team) == league.league.roster_size
    assert team.roster.filter(week=WEEK, player__position="PK").count() == 1


def test_humans_are_left_alone(league):
    """bot_moves never touches a team with an owner, even if its starter is out for the season."""
    starter = league.me.roster.filter(week=WEEK, slot=RosterEntry.Slot.RB).first()
    hurt(starter, Player.InjuryStatus.INJURED_RESERVE, SEASON_OVER)
    bot_moves(league.league)
    assert league.me.roster.filter(week=WEEK, player=starter.player).exists()
