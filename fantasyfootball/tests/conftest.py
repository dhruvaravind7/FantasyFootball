"""Shared test setup: a frozen clock, a tiny fake NFL, and a drafted 4-team league."""
import random
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import time_machine
from rest_framework.test import APIClient

from accounts.models import User
from fantasy.draft import autopick, start_draft, team_on_clock
from fantasy.models import League, ScoringRules
from league.models import Game, Player, PlayerGameStats, Season, Team, TeamGameStats

EASTERN = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=EASTERN)          # a Wednesday at noon
WEEK = 9
KICKOFF = datetime(2026, 11, 15, 13, 0, tzinfo=EASTERN)      # that Sunday, 1 PM
DEPTH = {"QB": 2, "RB": 4, "WR": 5, "TE": 2, "PK": 1}        # players per NFL team, by position
TEAMS = [("Seattle", "Hawks", "SEA"), ("Denver", "Horses", "DEN"), ("Miami", "Fins", "MIA"), ("Boston", "Pats", "BOS"),
         ("Dallas", "Stars", "DAL"), ("Chicago", "Bears", "CHI"), ("Austin", "Bats", "AUS"), ("Reno", "Aces", "REN")]


def stat_line(position: str, depth: int, strength: int) -> dict:
    """Last season's line for one player: better depth (1) and a stronger team mean more points."""
    boost = strength * 5 - depth * 20
    if position == "QB":
        return {"pass_yards": 260 + boost, "pass_tds": 2}
    if position == "PK":
        return {"fg_attempts": 2, "fg_made": 2, "fg_made_0_39": 2, "xp_attempts": 3, "xp_made": 3}
    return {"receptions": 6, "rec_yards": 90 + boost, "rush_yards": 40 if position == "RB" else 0}


@pytest.fixture
def clock():
    """Freeze 'now' at NOW. Tests move it with clock.move_to(...)."""
    with time_machine.travel(NOW, tick=False) as traveller:
        yield traveller


@pytest.fixture
def nfl(db, clock):
    """8 NFL teams with 14 players each, last season's stats (for rankings), and this week's 4 games on Sunday."""
    last = Season.objects.create(year=2025)
    season = Season.objects.create(year=2026, active=True, current_week=WEEK)
    teams = [Team.objects.create(city=c, name=n, abbreviation=a, conference="AFC", division="N") for c, n, a in TEAMS]

    # Every team gets DEPTH's players, named like "SEA" "WR3" (first name = team, last name = position + depth)
    Player.objects.bulk_create([
        Player(first_name=team.abbreviation, last_name=f"{position}{depth}", position=position, team=team)
        for team in teams for position, count in DEPTH.items() for depth in range(1, count + 1)
    ])

    # Pair the teams up (0 v 1, 2 v 3, ...). Each pair gets: a FINAL 2025 game with stats, a FINAL week-1 game
    # (fantasy_season_end needs it), and a SCHEDULED game this week at KICKOFF.
    for i in range(0, len(teams), 2):
        home, away = teams[i], teams[i + 1]
        old = Game.objects.create(season=last, week=1, home_team=home, away_team=away,
                                  kickoff=NOW - timedelta(days=365), status=Game.Status.FINAL)
        for strength, team in enumerate((home, away), start=i):
            TeamGameStats.objects.create(game=old, team=team, sacks=strength, points_allowed=14)
            PlayerGameStats.objects.bulk_create([
                PlayerGameStats(game=old, player=p, team=team, **stat_line(p.position, int(p.last_name[-1]), strength))
                for p in team.players.all()
            ])
        Game.objects.create(season=season, week=1, home_team=home, away_team=away,
                            kickoff=datetime(2026, 9, 13, 13, 0, tzinfo=EASTERN), status=Game.Status.FINAL)
        Game.objects.create(season=season, week=WEEK, home_team=home, away_team=away, kickoff=KICKOFF)
    return SimpleNamespace(season=season, teams=teams)


@pytest.fixture
def league(nfl):
    """A drafted 4-team league: two people ("Me" and "You") and two bots, all rosters full."""
    me_user = User.objects.create_user(email="me@x.com", username="me", password="pw")
    you_user = User.objects.create_user(email="you@x.com", username="you", password="pw")
    lg = League.objects.create(name="Test League", season=nfl.season, commissioner=me_user, size=4)
    ScoringRules.objects.create(league=lg)
    me = lg.teams.create(name="Me", owner=me_user)
    you = lg.teams.create(name="You", owner=you_user)
    random.seed(1)                           # the draft order is random; a fixed seed makes every run identical
    start_draft(lg)
    while True:
        lg.refresh_from_db()
        if lg.status != League.Status.DRAFTING:
            break
        autopick(lg, team_on_clock(lg))
    bots = list(lg.teams.filter(owner__isnull=True).order_by("name"))       # bots have no owner
    return SimpleNamespace(league=lg, me=me, you=you, bots=bots, me_user=me_user, you_user=you_user)


@pytest.fixture
def api():
    """api(user) -> an APIClient logged in as that user."""
    def make(user: User) -> APIClient:
        """A client for `user` (SERVER_NAME must be in ALLOWED_HOSTS)."""
        client = APIClient(SERVER_NAME="localhost")
        client.force_authenticate(user)
        return client
    return make
