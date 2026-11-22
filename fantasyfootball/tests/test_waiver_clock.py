from datetime import datetime

import pytest

from fantasy.waiver_clock import last_run, next_run, start_hold, waiver_clears_at
from league.models import Player

from .conftest import EASTERN, KICKOFF


@pytest.mark.parametrize("now, expected", [
    (datetime(2026, 11, 14, 2, 59, tzinfo=EASTERN), datetime(2026, 11, 14, 3, 0, tzinfo=EASTERN)),   # 2:59 -> today's run
    (datetime(2026, 11, 14, 3, 0, tzinfo=EASTERN), datetime(2026, 11, 15, 3, 0, tzinfo=EASTERN)),    # exactly 3:00 -> tomorrow's run
    (datetime(2026, 11, 14, 15, 0, tzinfo=EASTERN), datetime(2026, 11, 15, 3, 0, tzinfo=EASTERN)),   # 3 PM -> tomorrow's run
])
def test_next_run_is_strictly_after(now, expected):
    """next_run always returns a 3 AM ET run that is after `now`, never equal to it."""
    assert next_run(now) == expected


def test_next_run_keeps_3am_across_daylight_saving():
    """The night clocks fall back (Nov 1, 2026), the next run is still 3 AM Eastern."""
    run = next_run(datetime(2026, 10, 31, 22, 0, tzinfo=EASTERN))
    assert run.astimezone(EASTERN).hour == 3


def test_last_run_at_exactly_3am_is_that_run():
    """At 3:00:00 sharp, the latest run is the one happening now, not yesterday's."""
    run = datetime(2026, 11, 16, 3, 0, tzinfo=EASTERN)
    assert last_run(run) == run


def test_free_agent_is_not_on_waivers(league):
    """Nobody dropped him and his game hasn't started, so he's a free agent."""
    player = Player.objects.exclude(rosters__league=league.league).first()     # anyone not on a roster in this league
    assert waiver_clears_at(league.league, player) is None


def test_dropped_player_clears_at_first_run_two_days_later(league):
    """Dropped Wednesday at noon -> 2 days later is Friday noon -> the first run after that is Saturday 3 AM."""
    player = Player.objects.exclude(rosters__league=league.league).first()
    start_hold(league.league, player)
    assert waiver_clears_at(league.league, player) == datetime(2026, 11, 14, 3, 0, tzinfo=EASTERN)


def test_game_time_waivers_until_the_next_run(league, clock):
    """An hour after his game kicked off, he's on waivers until the next 3 AM run (Monday)."""
    player = Player.objects.exclude(rosters__league=league.league).first()
    clock.move_to(KICKOFF.replace(hour=14))                              # Sunday 2 PM
    assert waiver_clears_at(league.league, player) == datetime(2026, 11, 16, 3, 0, tzinfo=EASTERN)
