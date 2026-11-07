import random
from collections import Counter
from decimal import Decimal

from django.db import transaction

from .models import FantasyTeam, League, Matchup
from .rosters import ensure_league_week
from .scoring import league_week_totals

REGULAR_SEASON_WEEKS = 14


def round_robin(teams: list[FantasyTeam]) -> list[list[tuple[FantasyTeam, FantasyTeam]]]:
    """Every round of a round-robin (circle method): n-1 rounds, each team plays once per round, each pair meets once."""
    teams = list(teams)
    n = len(teams)
    rounds = []
    for _ in range(n - 1):
        rounds.append([(teams[i], teams[n-1-i]) for i in range(n // 2)])     # i pairs with the team i spots from the END
        teams = [teams[0]] + [teams[-1]] + teams[1:-1]      # keep teams[0]; the last team moves to spot 1
    return rounds

@transaction.atomic
def build_schedule(league: League) -> None:
    """Create every regular-season matchup from the draft week through week 14, from a shuffled round-robin."""
    teams = list(league.teams.all())
    random.shuffle(teams)
    rounds = round_robin(teams)
    start = league.season.current_week or 1
    home_games = Counter()            # team id -> home games given so far
    matchups = []
    for week in range(start, REGULAR_SEASON_WEEKS + 1):
        for a, b in rounds[(week - start) % len(rounds)]:      # week `start` uses round 0, the next week round 1, ... wrapping around
            # whichever has FEWER home games so far is home (a wins a tie)
            if home_games[a.id] <= home_games[b.id]:
                home = a
                away = b
            else:
                home = b
                away = a
            home_games[home.id] += 1
            matchups.append(Matchup(league=league, week=week, home=home, away=away))
    Matchup.objects.bulk_create(matchups)

def week_totals(league: League, week: int | None) -> dict[int, Decimal] | None:
    """Every team's total for `week`, or None for a future week (or when the season has no current week)."""
    current = league.season.current_week
    if week is None or current is None or week > current:
        return None
    if week == current:            # only the current week rolls rows forward
        ensure_league_week(league, week)
    return league_week_totals(league, week)


def matchup_scores(matchup: Matchup, totals: dict[int, Decimal] | None) -> tuple[Decimal | None, Decimal | None]:
    """(home, away) points: the saved scores once final, otherwise from `totals` (None, None for a future week)."""
    if matchup.is_final:
        return matchup.home_score, matchup.away_score
    if totals is None:
        return None, None
    return totals[matchup.home_id], totals[matchup.away_id]
