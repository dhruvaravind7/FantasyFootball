from collections import Counter

from django.db import IntegrityError, transaction
from rest_framework.exceptions import ValidationError

from league.models import Player, Team

from .draft import POSITION_LIMITS
from .lineups import entry_nfl_team_id, entry_position, healthy_on_ir, started_team_ids
from .models import FantasyTeam, League, RosterEntry
from .rankings import FANTASY_POSITIONS
from .rosters import ensure_week


def current_entries(team: FantasyTeam) -> tuple[int, dict[int, RosterEntry]]:
    """The current week and this team's rows for it ({entry id: RosterEntry}). 400 unless the league is In Season."""
    league = team.league
    if league.status != League.Status.IN_SEASON:
        raise ValidationError({"detail": "Adds and drops open after the draft."})
    week = league.season.current_week
    ensure_week(team, week)
    return week, {e.id: e for e in team.roster.filter(week=week).select_related("player", "nfl_team")}


@transaction.atomic
def add_player(team: FantasyTeam, player: Player | None = None, nfl_team: Team | None = None,
               drop_entry_id: int | None = None) -> RosterEntry:
    """Add a free agent (a player OR a D/ST) to this week's roster, dropping `drop_entry_id` first if given."""
    league = team.league
    week, entries = current_entries(team)

    # 1. The IR rule: no adds at all while a healthy player sits on IR
    healthy_ir_players = healthy_on_ir(entries.values())
    if healthy_ir_players:
        raise ValidationError({"detail": f"Move {healthy_ir_players[0]} off IR first; he's healthy."})

    # 2. What's being added: its position, its NFL team (for locks), and who already owns it this week
    if player is not None:
        if player.position not in FANTASY_POSITIONS or player.team_id is None:
            raise ValidationError({"player_id": f"{player} can't be added"})
        position, nfl_team_id = player.position, player.team_id
        owned = RosterEntry.objects.filter(league=league, week=week, player=player)
    else:
        position, nfl_team_id = "DST", nfl_team.id
        owned = RosterEntry.objects.filter(league=league, week=week, nfl_team=nfl_team)   # YOU: the same filter, for the D/ST
    owner = owned.select_related("team").first()
    if owner is not None:
        raise ValidationError({"detail": f"{owner.team.name} already has him"})

    # 3. Locked? (the added player's game, and the dropped player's game)
    started = started_team_ids(league.season, week)
    if nfl_team_id in started:
        raise ValidationError({"detail": f"{player if player else nfl_team}'s game has started; he can't be added until next week."})

    drop = None
    if drop_entry_id is not None:
        drop = entries.get(drop_entry_id)
        if drop is None:
            raise ValidationError({"drop_entry_id": f"{drop_entry_id} is not in your team"})
        if entry_nfl_team_id(drop) in started:
            raise ValidationError({"detail": f"{drop}'s game has started; he can't be dropped until next week"})

    # 4. Limits, counted WITHOUT the player being dropped
    keeping = [e for e in entries.values() if e is not drop]
    counts = Counter(entry_position(e) for e in keeping)
    if counts[position] >= POSITION_LIMITS[position]:
        raise ValidationError({"detail": f"You already have the most {position}s allowed"})
    if sum(1 for e in keeping if e.slot != RosterEntry.Slot.IR) >= league.roster_size:
        raise ValidationError({"detail": "Your roster is full; choose a player to drop."})

    # 5. Do it: drop first (frees his spot), then add to the bench. The savepoint catches a same-instant double add.
    if drop:
        drop.delete()
    try:
        with transaction.atomic():
            return RosterEntry.objects.create(team=team, league=league, week=week, player=player, nfl_team=nfl_team)
    except IntegrityError:
        raise ValidationError({"detail": "Someone just added him."})


@transaction.atomic
def drop_player(team: FantasyTeam, entry_id: int) -> None:
    """Drop one row from this week's roster. He becomes a free agent immediately."""
    league = team.league
    week, entries = current_entries(team)
    entry = entries.get(entry_id)
    if entry is None:
        raise ValidationError({"entry_id": "That player isn't on your roster this week."})
    stuck = healthy_on_ir(entries.values())
    if stuck and entry not in stuck:
        raise ValidationError({"detail": f"Move {stuck[0]} off IR first; he's healthy."})
    if entry_nfl_team_id(entry) in started_team_ids(league.season, week):
        raise ValidationError({"detail": f"{entry}'s game has started; he can't be dropped until next week."})
    entry.delete()