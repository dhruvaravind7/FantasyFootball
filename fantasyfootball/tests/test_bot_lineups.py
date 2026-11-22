"""Bot lineups (13d): who starts, who can't, locked rows, and the once-per-kickoff pregame check."""
from datetime import timedelta

import pytest

from fantasy import bot_lineups
from fantasy.bot_lineups import pregame_lineups, set_bot_lineups
from fantasy.models import RosterEntry
from league.models import Game, Player, PlayerGameStats

from .conftest import KICKOFF, WEEK

STARTING = [RosterEntry.Slot.WR, RosterEntry.Slot.FLEX]       # where a WR can start


@pytest.fixture
def bot(league):
    """The first bot and his first starting WR."""
    team = league.bots[0]
    return team, team.roster.filter(week=WEEK, slot=RosterEntry.Slot.WR).order_by("id").first()


def test_out_and_doubtful_players_sit(league, bot):
    """A starter who's Doubtful is benched."""
    team, wr = bot
    Player.objects.filter(pk=wr.player_id).update(injury_status=Player.InjuryStatus.DOUBTFUL)
    assert set_bot_lineups(league.league) >= 1
    wr.refresh_from_db()
    assert wr.slot == RosterEntry.Slot.BENCH


def test_bye_week_players_sit(league, bot):
    """Delete his NFL team's game this week (a bye): he and his teammates are benched."""
    team, wr = bot
    Game.objects.filter(season=league.league.season, week=WEEK).filter(home_team=wr.player.team).delete()
    Game.objects.filter(season=league.league.season, week=WEEK).filter(away_team=wr.player.team).delete()
    set_bot_lineups(league.league)
    wr.refresh_from_db()
    assert wr.slot == RosterEntry.Slot.BENCH


def test_this_seasons_average_beats_preseason(league, bot):
    """A bench WR with a huge game this season jumps into the lineup."""
    team, _ = bot
    sleeper = team.roster.filter(week=WEEK, slot=RosterEntry.Slot.BENCH, player__position="WR").order_by("id").first()
    week_one = Game.objects.filter(season=league.league.season, week=1).first()
    PlayerGameStats.objects.create(game=week_one, player=sleeper.player, team=sleeper.player.team, rec_yards=400)
    set_bot_lineups(league.league)
    sleeper.refresh_from_db()
    assert sleeper.slot in STARTING


def test_locked_rows_stay_put(league, bot, clock):
    """After kickoff a starter keeps his slot, even if he's ruled Out."""
    team, wr = bot
    clock.move_to(KICKOFF + timedelta(minutes=5))
    Player.objects.filter(pk=wr.player_id).update(injury_status=Player.InjuryStatus.OUT)
    set_bot_lineups(league.league)
    wr.refresh_from_db()
    assert wr.slot == RosterEntry.Slot.WR


def test_pregame_runs_once_per_kickoff(league, clock, monkeypatch):
    """Nothing on Wednesday. 30 minutes before kickoff: one injury sync, lineups set. A second pass does nothing."""
    calls = []
    monkeypatch.setattr(bot_lineups, "sync_injuries", lambda: calls.append(1))     # no real ESPN request
    assert pregame_lineups() == 0
    assert calls == []

    clock.move_to(KICKOFF - timedelta(minutes=30))
    pregame_lineups()
    league.league.refresh_from_db()
    assert calls == [1]
    assert league.league.lineups_checked_at is not None

    pregame_lineups()
    assert calls == [1]                                       # already checked for this kickoff
