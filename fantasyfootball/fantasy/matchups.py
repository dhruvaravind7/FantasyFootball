import random
from collections import Counter
from decimal import Decimal

from django.db import transaction
from django.db.models import F

from league.models import Game, Season

from .models import FantasyTeam, League, Matchup
from .rosters import ensure_league_week
from .scoring import league_week_totals

FIRST_ROUND = {
    4: [(1, 4), (2, 3)],        # slot 0: 1 v 4, slot 1: 2 v 3
    6: [(4, 5), (3, 6)],        # seeds 1 and 2 have a bye: slot 0's winner plays seed 1, slot 1's plays seed 2
}


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
    for week in range(start, league.regular_season_weeks + 1):
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

def week_is_over(season: Season, week: int) -> bool:
    """True when the week has NFL games and every one of them is final."""
    games = Game.objects.filter(season=season, week=week)
    return games.exists() and not games.exclude(status=Game.Status.FINAL).exists()


def finalize_due_weeks() -> int:
    """Finalize every league-week whose NFL games are all final. Returns how many league-weeks were finalized."""
    # .order_by() clears Matchup's default ordering ("id" is in it). Otherwise Django adds id to the SELECT DISTINCT,
    # and every matchup becomes its own "distinct" row instead of one row per league-week.
    pending = (Matchup.objects.filter(is_final=False, week__lte=F("league__season__current_week"))
               .values_list("league_id", "week").order_by().distinct())
    over = {}            # (season id, week) -> bool, so 10 leagues in the same week only check the games once
    done = 0
    for league_id, week in pending:
        league = League.objects.select_related("season").get(pk=league_id)
        key = (league.season_id, week)
        if key not in over:
            over[key] = week_is_over(league.season, week)
        if over[key]:
            finalize_week(league, week)
            done += 1
    return done


@transaction.atomic
def finalize_week(league: League, week: int) -> int:
    """Save every unfinished matchup's final score for `week`. Returns how many matchups were finalized."""
    unfinished = list(league.matchups.select_for_update().filter(week=week, is_final=False))
    if not unfinished:
        return 0

    ensure_league_week(league, week)
    totals = league_week_totals(league, week)
    for matchup in unfinished:
        matchup.home_score = totals[matchup.home_id]
        matchup.away_score = totals[matchup.away_id]
        matchup.is_final = True
    Matchup.objects.bulk_update(unfinished, ["home_score", "away_score", "is_final"])
    if week == league.regular_season_weeks:
        start_playoffs(league)
    elif week > league.regular_season_weeks:          # the games just finalized were playoff games
        next_round(league, unfinished[0].playoff_round, week)

    return len(unfinished)


def standings(league: League) -> list[dict]:
    """Every team's regular-season record from final matchups, ranked by win % then points for."""
    table = {
        team.id: {"team": team, "wins": 0, "losses": 0, "ties": 0,
                  "points_for": Decimal("0"), "points_against": Decimal("0")}
        for team in league.teams.select_related("owner")
    }
    for m in league.matchups.filter(is_final=True, playoff_round=None):
        home, away = table[m.home_id], table[m.away_id]
        home["points_for"] += m.home_score
        home["points_against"] += m.away_score
        away["points_for"] += m.away_score
        away["points_against"] += m.home_score
        if m.home_score > m.away_score:
            home["wins"] += 1
            away["losses"] += 1
        elif m.home_score < m.away_score:
            home["losses"] += 1
            away["wins"] += 1
        else:
            home["ties"] += 1
            away["ties"] += 1

    rows = list(table.values())
    for row in rows:
        games = row["wins"] + row["losses"] + row["ties"]
        row["win_pct"] = (Decimal(row["wins"]) + Decimal(row["ties"]) / 2) / games if games else Decimal("0")
    rows.sort(key=lambda row: (row["win_pct"], row["points_for"]), reverse=True)
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return rows

def winner(matchup: Matchup) -> FantasyTeam:
    """The team that advances: the higher score, or the better (lower-numbered) seed on a tie."""
    if matchup.home_score != matchup.away_score:
        return matchup.home if matchup.home_score > matchup.away_score else matchup.away
    return matchup.home if matchup.home.playoff_seed < matchup.away.playoff_seed else matchup.away

def start_playoffs(league: League) -> None:
    """Seed the top `playoff_teams` from the standings and create the first playoff round."""
    seeded = standings(league)[:league.playoff_teams]
    teams = {row["rank"]: row["team"] for row in seeded}          # seed -> FantasyTeam
    for rank in teams:
        teams[rank].playoff_seed = rank
    FantasyTeam.objects.bulk_update(teams.values(), ["playoff_seed"])
    week = league.regular_season_weeks + 1
    Matchup.objects.bulk_create([
        Matchup(league=league, week=week, home=teams[a], away=teams[b], playoff_round=1, bracket_slot=slot)
        for slot, (a, b) in enumerate(FIRST_ROUND[league.playoff_teams])
    ])

def next_round(league: League, finished_round: int, week: int) -> None:
    """Create the round after `finished_round` (played in `week`), or mark the league Complete after the final."""
    games = list(league.matchups.filter(playoff_round=finished_round).select_related("home", "away").order_by("bracket_slot"))
    winners = [winner(m) for m in games]
    if len(winners) == 1:
        league.status = League.Status.COMPLETE
        league.save(update_fields=["status"])
        return
    if finished_round == 1 and league.playoff_teams == 6:
        byes = {team.playoff_seed: team for team in league.teams.filter(playoff_seed__in=[1, 2])}
        pairs = [(byes[1], winners[0]), (byes[2], winners[1])]
    else:
        home, away = winners
        if away.playoff_seed < home.playoff_seed:
            home, away = away, home
        pairs = [(home, away)]
    Matchup.objects.bulk_create([
        Matchup(league=league, week=week + 1, home=a, away=b, playoff_round=finished_round + 1, bracket_slot=slot)
        for slot, (a, b) in enumerate(pairs)
    ])
