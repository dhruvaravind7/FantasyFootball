"""The schedule, finalizing a week, standings, and the 4-team playoff bracket to a champion."""
from collections import Counter
from decimal import Decimal

from fantasy import matchups
from fantasy.matchups import finalize_due_weeks, finalize_week, standings
from fantasy.models import League
from league.models import Game

from .conftest import WEEK


def score_week(league: League, week: int, scores: dict[int, int]) -> None:
    """Save final scores straight onto a week's matchups ({fantasy team id: points}), skipping real stats."""
    for m in league.matchups.filter(week=week):
        m.home_score, m.away_score, m.is_final = scores[m.home_id], scores[m.away_id], True
        m.save()


def test_schedule_everyone_plays_once_a_week(league):
    """From the draft week through the last regular-season week, every team plays exactly once each week."""
    lg = league.league
    for week in range(WEEK, lg.regular_season_weeks + 1):
        games = list(lg.matchups.filter(week=week))
        playing = Counter([m.home_id for m in games] + [m.away_id for m in games])
        assert len(games) == 2
        assert sorted(playing.values()) == [1, 1, 1, 1]
    assert not lg.matchups.filter(week=lg.regular_season_weeks + 1).exists()


def test_finalize_waits_for_every_nfl_game(league):
    """Week 9 isn't over while games are scheduled. Once they're all final, its matchups get scores."""
    assert finalize_due_weeks() == 0
    Game.objects.filter(season=league.league.season, week=WEEK).update(status=Game.Status.FINAL)
    assert finalize_due_weeks() == 1
    assert not league.league.matchups.filter(week=WEEK, is_final=False).exists()


def test_standings_rank_by_win_pct_then_points(league):
    """Team ranks follow wins; a tie is half a win."""
    lg = league.league
    a, b, c, d = lg.teams.order_by("id")
    score_week(lg, WEEK, {a.id: 120, b.id: 100, c.id: 90, d.id: 80})
    score_week(lg, WEEK + 1, {a.id: 110, b.id: 110, c.id: 70, d.id: 60})
    rows = standings(lg)
    assert rows[0]["team"] == a                     # never lost
    assert rows[-1]["team"] == d                    # never won
    assert all(row["wins"] + row["losses"] + row["ties"] == 2 for row in rows)


def test_playoffs_to_a_champion(league, monkeypatch):
    """Seed 1 is the best team. Round 1 is 1 v 4 and 2 v 3; the final's home team is the better seed; then COMPLETE."""
    lg = league.league
    teams = list(lg.teams.order_by("id"))
    points = {t.id: 100 - 10 * i for i, t in enumerate(teams)}       # teams[0] scores most every week
    for week in range(WEEK, lg.regular_season_weeks):
        score_week(lg, week, points)
    finalize_week_with(monkeypatch, lg, lg.regular_season_weeks, points)          # the last regular week starts the playoffs

    first = list(lg.matchups.filter(playoff_round=1).order_by("bracket_slot"))
    assert [(m.home.playoff_seed, m.away.playoff_seed) for m in first] == [(1, 4), (2, 3)]
    assert first[0].home == teams[0]

    upset = {teams[0].id: 50, teams[3].id: 60, teams[1].id: 90, teams[2].id: 90}   # 4 beats 1; 2 v 3 ties -> seed 2
    finalize_week_with(monkeypatch, lg, lg.regular_season_weeks + 1, upset)
    final = lg.matchups.get(playoff_round=2)
    assert (final.home, final.away) == (teams[1], teams[3])           # seed 2 hosts seed 4

    finalize_week_with(monkeypatch, lg, lg.regular_season_weeks + 2, {teams[1].id: 0, teams[3].id: 1})
    lg.refresh_from_db()
    assert lg.status == League.Status.COMPLETE


def finalize_week_with(monkeypatch, league: League, week: int, scores: dict[int, int]) -> None:
    """Run the real finalize_week, but with these totals instead of real stats (so the bracket logic is what's tested)."""
    monkeypatch.setattr(matchups, "league_week_totals", lambda lg, wk: {k: Decimal(v) for k, v in scores.items()})
    finalize_week(league, week)
