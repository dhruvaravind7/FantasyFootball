from collections.abc import Iterable

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from league.models import Game, Player, Season

from .models import RosterEntry, League, FantasyTeam
from .rosters import ensure_week

# Which positions may play in each starting slot. BENCH takes anyone, and IR is handled separately.
SLOT_POSITIONS = {"QB": {"QB"}, "RB": {"RB"}, "WR": {"WR"}, "TE": {"TE"}, "FLEX": {"RB", "WR", "TE"}, "K": {"PK"}, "DST": {"DST"}}
IR_STATUSES = {Player.InjuryStatus.OUT, Player.InjuryStatus.INJURED_RESERVE}


def slot_capacity(league: League, slot: str) -> int:
    """How many players fit in `slot` in this league."""
    return {
        "QB": league.qb_slots, "RB": league.rb_slots, "WR": league.wr_slots, "TE": league.te_slots, "FLEX": league.flex_slots,
        "K": league.k_slots, "DST": league.dst_slots, "BENCH": league.bench_slots, "IR": league.ir_slots,
    }[slot]


def entry_position(entry: RosterEntry) -> str:
    """'DST' for a D/ST row, otherwise the player's position ("QB", "RB", "PK", ...)."""
    if entry.nfl_team_id is not None:
        return "DST"
    else:
        return entry.player.position


def entry_nfl_team_id(entry: RosterEntry) -> int:
    """The NFL team whose kickoff decides when this row locks."""
    return entry.nfl_team_id or entry.player.team_id

def can_play(entry: RosterEntry, slot: str) -> bool:
    """Is this row allowed in `slot`?"""
    if slot == "BENCH":
        return True
    if slot == "IR":
        # Only players (never a D/ST) whose injury_status is Out or IR
        return entry.nfl_team_id is None and entry.player.injury_status in IR_STATUSES
    return entry_position(entry) in SLOT_POSITIONS[slot]

def started_team_ids(season: Season, week: int) -> set[int]:
    """IDs of every NFL team whose game this week has kicked off."""
    games = Game.objects.filter(season=season, week=week).filter(
        Q(kickoff__lte=timezone.now()) | ~Q(status=Game.Status.SCHEDULED)
    )
    ids = set()
    for home_id, away_id in games.values_list("home_team_id", "away_team_id"):
        ids.update((home_id, away_id))
    return ids

def healthy_on_ir(entries: Iterable[RosterEntry]) -> list[RosterEntry]:
    """Rows sitting on IR whose player isn't Out or IR anymore. While any exist, the team is blocked."""
    return [e for e in entries if e.slot == RosterEntry.Slot.IR and not can_play(e, e.slot)]

@transaction.atomic
def move(team: FantasyTeam, entry_id: int, slot: str, swap_with: int | None = None) -> None:
    """Put a roster row into `slot` for the current week, swapping with `swap_with` if the slot is full."""
    league = team.league
    week = league.season.current_week
    ensure_week(team, week)
    team_players = {e.id: e for e in team.roster.filter(week=week).select_related("player")}

    # 1. The row must be on this week's roster
    team_player = team_players.get(entry_id)
    if team_player is None:
        raise ValidationError({"entry_id": f"Entry {entry_id} isn't on this week's roster."})

    # 2. The IR rule: a healthy player on IR blocks everything EXCEPT moving him
    stuck = healthy_on_ir(team_players.values())
    if stuck and team_player not in stuck:
        raise ValidationError({"detail": f"Move {stuck[0].player} off IR first; he's healthy."})

    # 3. Locked? Then he can't move at all.
    started = started_team_ids(league.season, week)
    if entry_nfl_team_id(team_player) in started:
        raise ValidationError({"detail": f"{team_player}'s game has started; he's locked."})

    # 4. Allowed in that slot?
    if not can_play(team_player, slot):
        raise ValidationError({"slot": f"{team_player} can't play {slot}."})

    if slot == team_player.slot:
        return                                                   # already there, nothing to do

    # 5. Room in the slot? Just move him.
    occupants = [e for e in team_players.values() if e.slot == slot]
    if len(occupants) < slot_capacity(league, slot):
        team_player.slot = slot
        team_player.save(update_fields=["slot"])
        return

    # 6. Full: swap with the named row. It must be IN that slot, not locked, and allowed in entry's OLD slot.
    other = team_players.get(swap_with)
    if other is None or other.slot != slot:
        raise ValidationError({"swap_with": f"{slot} is full; say which player to swap with."})
    if entry_nfl_team_id(other) in started:
        raise ValidationError({"detail": f"{other}'s game has started; he's locked."})
    if not can_play(other, team_player.slot):
        raise ValidationError({"swap_with": f"{other} can't play {team_player.slot}."})

    # Python can swap two values in one line: a, b = b, a
    other.slot, team_player.slot = team_player.slot, slot
    RosterEntry.objects.bulk_update([team_player, other], ["slot"])