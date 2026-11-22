from datetime import datetime

import pytest

from fantasy.models import WaiverClaim, WaiverHold
from fantasy.waivers import place_claim, process_due_waivers
from league.models import Player

from .conftest import EASTERN


@pytest.fixture
def on_waivers(league):
    """Three unowned WRs ("Collins", "Bourne", "Extra"), each on waivers until Thursday 3 AM."""
    free = list(Player.objects.filter(position="WR").exclude(rosters__league=league.league).order_by("id")[:3])
    for player in free:
        WaiverHold.objects.create(league=league.league, player=player, clears_at=datetime(2026, 11, 12, 3, 0, tzinfo=EASTERN))
    return free


def bench_row(team, n=0):
    """The team's n-th bench row this week (a player to drop)."""
    return team.roster.filter(week=team.league.season.current_week, slot="BENCH").order_by("id")[n]


def test_rolling_priority_a_b_c(league, on_waivers, clock):
    """The 13b example: a failed claim keeps your spot, a win sends you to the bottom, a loss costs nothing."""
    a, b, c = league.me, league.you, league.bots[0]
    for priority, team in enumerate([a, b, c, league.bots[1]], start=1):
        team.waiver_priority = priority
        team.save()
    collins, bourne, extra = on_waivers
    place_claim(a, player=extra)                                         # no drop with a full roster -> FAILED
    place_claim(a, player=collins, drop_entry_id=bench_row(a).id)
    place_claim(b, player=collins, drop_entry_id=bench_row(b).id)
    place_claim(b, player=bourne, drop_entry_id=bench_row(b, 1).id)
    place_claim(c, player=bourne, drop_entry_id=bench_row(c).id)

    clock.move_to(datetime(2026, 11, 12, 3, 1, tzinfo=EASTERN))         # Thursday 3:01 AM ET, just after the run
    assert process_due_waivers() == 2

    status = {(cl.team_id, cl.player_id): cl.status for cl in WaiverClaim.objects.all()}
    assert status[(a.id, extra.id)] == WaiverClaim.Status.FAILED
    assert status[(a.id, collins.id)] == WaiverClaim.Status.WON
    assert status[(b.id, collins.id)] == WaiverClaim.Status.LOST
    assert status[(b.id, bourne.id)] == WaiverClaim.Status.WON
    assert status[(c.id, bourne.id)] == WaiverClaim.Status.LOST
    order = list(league.league.teams.order_by("waiver_priority").values_list("name", flat=True))
    assert order == [c.name, league.bots[1].name, a.name, b.name]


def test_claims_wait_until_the_player_clears(league, on_waivers, clock):
    """At Thursday 2 AM the latest run is Wednesday's, before the player clears, so the claim stays PENDING."""
    claim = place_claim(league.me, player=on_waivers[0], drop_entry_id=bench_row(league.me).id)
    clock.move_to(datetime(2026, 11, 12, 2, 0, tzinfo=EASTERN))         # Thursday 2:00 AM ET
    process_due_waivers()
    claim.refresh_from_db()                                              # re-read: the run changed it in the database
    assert claim.status == WaiverClaim.Status.PENDING
