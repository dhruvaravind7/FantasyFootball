from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import F

from league.models import Player, PlayerGameStats, TeamGameStats

from .models import PlayerRanking
from .scoring import dst_points, player_points

FANTASY_POSITIONS = ["QB", "RB", "WR", "TE", "PK"]
SHRINK_GAMES = 4            # a player's average counts as if he also played 4 average games
SLOTS_FOR = {"QB": "qb_slots", "RB": "rb_slots", "WR": "wr_slots", "TE": "te_slots", "PK": "k_slots", "DST": "dst_slots"}


def draftable_players():
    """Every player at a fantasy position who is currently on an NFL team."""
    return Player.objects.filter(position__in=FANTASY_POSITIONS).exclude(team=None)


def last_season_totals(league, rules) -> dict:
    """{("player", id) or ("dst", team_id): {"position", "points", "games"}} from last season's stat lines."""
    year = league.season.year - 1
    totals = {}

    lines = PlayerGameStats.objects.filter(game__season__year=year, player__in=draftable_players()).select_related("player")
    for line in lines:
        key = ("player", line.player_id)
        if key not in totals:
            totals[key] = {"position": line.player.position, "points": Decimal("0"), "games": 0}
        totals[key]["points"] += player_points(line, rules)
        totals[key]["games"] += 1

    lines = TeamGameStats.objects.filter(game__season__year=year)
    for line in lines:
        key = ("dst", line.team_id)
        if key not in totals:
            totals[key] = {"position": "DST", "points": Decimal("0"), "games": 0}
        totals[key]["points"] += dst_points(line, rules)
        totals[key]["games"] += 1
    
    return totals


def value_over_replacement(league, totals) -> list[tuple]:
    """[(key, position, adjusted points per game, value over replacement), ...]"""
    by_position = defaultdict(list)
    for key, t in totals.items():
        by_position[t["position"]].append((key, t))

    results = []
    for position, group in by_position.items():
        # 1. The position's average points per game: all its points ÷ all its games
        average = sum(t["points"] for _, t in group) / sum(t["games"] for _, t in group)

        # 2. Each player's adjusted average, pulled toward `average`:
        #       (his points + SHRINK_GAMES × average) ÷ (his games + SHRINK_GAMES)
        adjusted = [(key, (t["points"] + SHRINK_GAMES * average) / (t["games"] + SHRINK_GAMES)) for key, t in group]
        adjusted.sort(key=lambda pair: pair[1], reverse=True)          # best first

        # 3. How many start at this position across the league. getattr reads a field by name,
        #    like setattr in 8b: getattr(league, "qb_slots") is league.qb_slots
        starters = league.size * getattr(league, SLOTS_FOR[position])
        if position in ("RB", "WR"):
            starters += ((league.size * league.flex_slots) // 2)                # half the league's FLEX spots each: size × flex_slots // 2

        # 4. Replacement = the first NON-starter (index `starters`, since lists start at 0).
        #    min(...) protects against a position with fewer players than starters.
        replacement = adjusted[min(starters, len(adjusted) - 1)][1]

        for key, adj in adjusted:
            results.append((key, position, adj, adj-replacement))    # value = adjusted minus replacement
    return results


@transaction.atomic
def build_rankings(league) -> int:
    """Rank everything for this league, replacing any old ranking. Returns how many rows."""
    rules = league.scoring
    results = value_over_replacement(league, last_season_totals(league, rules))
    results.sort(key=lambda r: r[3], reverse=True)                     # highest value first

    rows = []
    for rank, (key, position, adj, value) in enumerate(results, start=1):
        kind, id_ = key
        rows.append(PlayerRanking(
            league=league, rank=rank, position=position, points_per_game=adj, value=value,
            player_id=id_ if kind == "player" else None,
            nfl_team_id=id_ if kind == "dst" else None,                                            # the D/ST version of the line above
        ))

    # Players with no history: after everyone else, by NFL draft pick, undrafted (no pick) last, then by name
    ranked_ids = [id_ for (kind, id_), *_ in results if kind == "player"]
    no_history = draftable_players().exclude(id__in=ranked_ids).order_by(F("nfl_draft_pick").asc(nulls_last=True), "last_name")
    for rank, player in enumerate(no_history, start=len(rows) + 1):
        rows.append(PlayerRanking(league=league, rank=rank, position=player.position, player=player))

    PlayerRanking.objects.filter(league=league).delete()
    PlayerRanking.objects.bulk_create(rows)
    return len(rows)