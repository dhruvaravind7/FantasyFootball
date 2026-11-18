from collections import Counter
from collections.abc import Iterator
from datetime import date, timedelta

from django.db.models import Min
from rest_framework.exceptions import ValidationError

from league.models import Player, Season

from .draft import SLOT_FOR_POSITION, FLEX_POSITIONS, POSITION_LIMITS
from .free_agency import add_player
from .lineups import IR_STATUSES, entry_position, move, entry_nfl_team_id, started_team_ids
from .models import PLAYOFF_LAST_WEEK, FantasyTeam, League, PlayerRanking, RosterEntry, WaiverClaim
from .rosters import ensure_week
from .waiver_clock import waiver_clears_at, start_hold
from .waivers import place_claim

UNRANKED = 10**6

def bot_moves(league: League) -> int:
    """Every bot: players back from IR, out-for-season drops, hurt players to IR (or a better swap), then open spots. Returns how many moves."""
    week = league.season.current_week
    season_end = fantasy_season_end(league.season)
    ranks = {(r.player_id, r.nfl_team_id): r.rank for r in league.rankings.all()}
    started = started_team_ids(league.season, week)
    moves = 0

    def rank(e: RosterEntry) -> int:
        """This row's preseason rank (1 = best), or UNRANKED."""
        return ranks.get((e.player_id, e.nfl_team_id), UNRANKED)

    for team in league.teams.filter(owner=None):
        ensure_week(team, week)

        def roster() -> list[RosterEntry]:
            """This team's rows this week, read fresh (every move changes them)."""
            return list(team.roster.filter(week=week).select_related("player"))

        # 1. Healthy again on IR -> back, dropping the worst of (bench + him) if the roster is full
        for e in [e for e in roster() if e.slot == RosterEntry.Slot.IR and not is_hurt(e)]:  # on IR, and not hurt
            entries = roster()
            active = [x for x in entries if x.slot != RosterEntry.Slot.IR]
            if len(active) >= league.roster_size:
                bench = [x for x in entries if x.slot == RosterEntry.Slot.BENCH and entry_nfl_team_id(x) not in started]          # on the bench, and his game hasn't started
                worst = max(bench + [e], key=rank)
                worst.delete()
                start_hold(league, worst.player, worst.nfl_team)
                moves += 1
                if worst is e:
                    continue                 # he was the worst, so he's gone, and there's nothing to move
            if move_off_ir(team, e):
                moves += 1

        # 2. Out for the season -> drop him for the best available at his position
        for e in [e for e in roster() if e.slot != RosterEntry.Slot.IR and out_for_season(e.player, season_end)]:            # not on IR, and out for the season
            if replacing(team, e):
                continue
            choice = next(available(league, team, week, season_end, e.player.position), None)
            if choice and acquire(team, choice, e.id):
                moves += 1

        # 3. Hurt but coming back
        for e in [e for e in roster() if e.slot != RosterEntry.Slot.IR and is_hurt(e) and not out_for_season(e.player, season_end)]:    # not on IR, hurt, NOT out for the season
            entries = roster()
            # 3a. Room on IR -> IR, then pick up someone at his position (no drop: IR frees his spot)
            if len([ir for ir in entries if ir.slot == RosterEntry.Slot.IR]) < league.ir_slots:                               # how many rows are on IR now
                try:
                    move(team, e.id, RosterEntry.Slot.IR)
                except ValidationError:
                    continue
                moves += 1
                choice = next(available(league, team, week, season_end, e.player.position), None)
                if choice and acquire(team, choice, None):
                    moves += 1
                continue
            # 3b. IR full -> the worse of (him, worst bench) goes, but ONLY for someone better at that position
            bench = [x for x in entries if x.slot == RosterEntry.Slot.BENCH and x.id != e.id]
            worst = max(bench + [e], key=rank)
            if replacing(team, worst):
                continue
            choice = next(available(league, team, week, season_end, entry_position(worst)), None)
            if choice and choice.rank < rank(worst) and acquire(team, choice, worst.id):   # choice ranks better than worst
                moves += 1

        # 4. Open spots left -> best available, never a 2nd K or D/ST
        entries = roster()
        counts = Counter(entry_position(e) for e in entries)
        waiting = team.claims.filter(status=WaiverClaim.Status.PENDING, drop_player=None, drop_nfl_team=None).count()
        open_spots = league.roster_size - sum(1 for e in entries if e.slot != RosterEntry.Slot.IR) - waiting
        for choice in available(league, team, week, season_end):
            if open_spots <= 0:
                break
            if choice.position in ("PK", "DST") and counts[choice.position] > 0:
                continue
            if counts[choice.position] >= POSITION_LIMITS[choice.position]:
                continue
            if acquire(team, choice, None):
                counts[choice.position] += 1
                open_spots -= 1
                moves += 1

    return moves

def acquire(team: FantasyTeam, choice: PlayerRanking, drop_entry_id: int | None) -> bool:
    """Claim `choice` if he's on waivers, otherwise add him now. False if it couldn't be done."""
    try:
        if waiver_clears_at(league=team.league, player=choice.player, nfl_team=choice.nfl_team) is not None:
            place_claim(team, choice.player, choice.nfl_team, drop_entry_id=drop_entry_id)
        else:
            add_player(team=team, player=choice.player, nfl_team=choice.nfl_team, drop_entry_id=drop_entry_id)
    except ValidationError:
        return False
    return True

def available(league: League, team: FantasyTeam, week: int, season_end: date | None,
              position: str | None = None) -> Iterator[PlayerRanking]:
    """Unowned, healthy ranked choices this week, best first, skipping any this team already has a pending claim on."""
    owned = RosterEntry.objects.filter(league=league, week=week)
    claimed = team.claims.filter(status=WaiverClaim.Status.PENDING)
    rankings = (league.rankings
                .exclude(player_id__in=owned.exclude(player=None).values("player_id"))
                .exclude(nfl_team_id__in=owned.exclude(nfl_team=None).values("nfl_team_id"))                # owned D/STs (same shape as the line above)
                .exclude(player_id__in=claimed.exclude(player=None).values("player_id"))                  # players this team already claimed
                .exclude(nfl_team_id__in=claimed.exclude(nfl_team=None).values("nfl_team_id"))                # D/STs this team already claimed
                .select_related("player", "nfl_team"))
    if position:
        rankings = rankings.filter(position=position)
    for r in rankings:
        if r.player is None or (r.player.team_id and r.player.injury_status not in IR_STATUSES
                                and not out_for_season(r.player, season_end)):     # not Out/IR now, and not out for the season
            yield r

def out_for_season(player: Player | None, season_end: date | None) -> bool:
    """True if this player won't be back before the fantasy season ends. A D/ST (None) never is."""
    return (player is not None and season_end is not None
            and player.return_date is not None and player.return_date > season_end)

def fantasy_season_end(season: Season) -> date | None:
    """Roughly the last day of fantasy week 17: 17 weeks after week 1's first kickoff. None if week 1 isn't loaded."""
    first = season.games.filter(week=1).aggregate(first=Min("kickoff"))["first"]     # the earliest week-1 kickoff
    return (first + timedelta(weeks=PLAYOFF_LAST_WEEK)).date() if first else None

def is_hurt(entry: RosterEntry) -> bool:
    """Is this row a player who's Out or on NFL IR right now? A D/ST never is."""
    return entry.player is not None and entry.player.injury_status in IR_STATUSES

def replacing(team: FantasyTeam, entry: RosterEntry) -> bool:
    """Is one of this team's PENDING claims already set to drop this row's player/D/ST?"""
    return team.claims.filter(status=WaiverClaim.Status.PENDING, drop_player=entry.player, drop_nfl_team=entry.nfl_team).exists()

def move_off_ir(team: FantasyTeam, entry: RosterEntry) -> bool:
    """Move a healthy player off IR into the first slot he fits that has room: bench, his position, then FLEX."""
    position = entry_position(entry)
    slots = [RosterEntry.Slot.BENCH, SLOT_FOR_POSITION[position]] + (["FLEX"] if position in FLEX_POSITIONS else [])
    for slot in slots:
        try:
            move(team, entry.id, slot)
            return True
        except ValidationError:
            continue                         # that slot is full (or not allowed), so try the next one
    return False
