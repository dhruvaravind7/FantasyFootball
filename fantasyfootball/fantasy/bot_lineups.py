from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import Min
from django.utils import timezone

from league.models import Game, Player, PlayerGameStats, Season, TeamGameStats
from league.sync import sync_injuries

from .lineups import SLOT_POSITIONS, entry_nfl_team_id, entry_position, slot_capacity, started_team_ids
from .models import FantasyTeam, League, RosterEntry
from .scoring import dst_points, player_points

LINEUP_LEAD = timedelta(hours=1)        # how long before a kickoff the pregame check runs
STARTING_ORDER = ["QB", "RB", "WR", "TE", "K", "DST", "FLEX"]   # FLEX last: it takes the best leftover
CANT_START = {Player.InjuryStatus.OUT, Player.InjuryStatus.DOUBTFUL, Player.InjuryStatus.INJURED_RESERVE}


def pregame_lineups(now: datetime | None = None) -> int:
    """If any in-season league's next kickoff is under LINEUP_LEAD away and it hasn't been checked for that kickoff yet:
    re-sync injuries once, then set every such league's bot lineups. Returns how many rows changed."""
    now = now or timezone.now()

    # 1. Which leagues are due?
    due = []
    for league in League.objects.filter(status=League.Status.IN_SEASON).select_related("season"):
        next_kickoff = Game.objects.filter(season=league.season, status=Game.Status.SCHEDULED, kickoff__gt=now).aggregate(first=Min("kickoff"))["first"]
        if next_kickoff and now >= next_kickoff - LINEUP_LEAD and (
                league.lineups_checked_at is None or league.lineups_checked_at < next_kickoff - LINEUP_LEAD):
            due.append(league)
    if not due:
        return 0

    # 2. One ESPN request for everyone. If ESPN is down, still set lineups with the statuses we have.
    try:
        sync_injuries()
    except Exception:
        pass

    # 3. Lineups, and remember the check
    changed = 0
    for league in due:
        changed += set_bot_lineups(league)
        league.lineups_checked_at = now
        league.save(update_fields=["lineups_checked_at"])
    return changed

def set_bot_lineups(league: League) -> int:
    """Start each bot's best healthy, unlocked players (FLEX last) and bench the rest. Returns how many rows changed slot."""
    week = league.season.current_week
    started = started_team_ids(league.season, week)
    playing = playing_team_ids(league.season, week)
    preseason = {(r.player_id, r.nfl_team_id): r.points_per_game or Decimal("0") for r in league.rankings.all()}
    changed = 0

    for team in league.teams.filter(owner=None):
        entries = list(team.roster.filter(week=week).select_related("player"))
        averages = season_averages(league, entries)

        def value(e: RosterEntry) -> Decimal:
            """This season's points per game, else preseason, else 0."""
            key = (e.player_id, e.nfl_team_id)
            return averages.get(key, preseason.get(key, Decimal("0")))

        # 1. Rows that can't move: on IR, or their game has started. They keep their slot (and use up room in it).
        fixed = [e for e in entries if e.slot == RosterEntry.Slot.IR or entry_nfl_team_id(e) in started]
        free = [e for e in entries if e not in fixed]
        room = {slot: slot_capacity(league, slot) for slot in STARTING_ORDER}
        for e in fixed:
            if e.slot in room:
                room[e.slot] -= 1

        # 2. Who may start: playing this week, and not Out/Doubtful/IR (a D/ST always may). Best first.
        starters = sorted([e for e in free if entry_nfl_team_id(e) in playing and
                           (e.player is None or e.player.injury_status not in CANT_START)], key=value, reverse=True)

        # 3. Fill each slot in order with the best starters who fit and aren't placed yet
        new_slot = {e.id: RosterEntry.Slot.BENCH for e in free}
        for slot in STARTING_ORDER:
            for e in starters:
                if room[slot] == 0:
                    break
                if new_slot[e.id] == RosterEntry.Slot.BENCH and entry_position(e) in SLOT_POSITIONS[slot]:
                    new_slot[e.id] = slot
                    room[slot] -= 1

        # 4. Save only the rows that changed, in one query
        moved = [e for e in free if e.slot != new_slot[e.id]]
        for e in moved:
            e.slot = new_slot[e.id]
        RosterEntry.objects.bulk_update(moved, ["slot"])
        changed += len(moved)
    return changed

def season_averages(league: League, entries: list[RosterEntry]) -> dict[tuple, Decimal]:
    """{(player_id, nfl_team_id): fantasy points per game this season} from FINAL games, under this league's scoring."""
    rules = league.scoring
    games = defaultdict(list)                # key -> [points in game 1, points in game 2, ...]
    for line in PlayerGameStats.objects.filter(game__season=league.season, game__status=Game.Status.FINAL,
                                               player_id__in=[e.player_id for e in entries if e.player_id]):
        games[(line.player_id, None)].append(player_points(line, rules))
    for line in TeamGameStats.objects.filter(game__season=league.season, game__status=Game.Status.FINAL,
                                             team_id__in=[e.nfl_team_id for e in entries if e.nfl_team_id]):
        games[(None, line.team_id)].append(dst_points(line, rules))
    return {key: sum(points) / len(points) for key, points in games.items()}

def playing_team_ids(season: Season, week: int) -> set[int]:
    """IDs of every NFL team with a game this week. A team not in here is on bye."""
    games = Game.objects.filter(season=season, week=week)
    ids = set()
    for home_id, away_id in games.values_list("home_team_id", "away_team_id"):
        ids.update((home_id, away_id))
    return ids
