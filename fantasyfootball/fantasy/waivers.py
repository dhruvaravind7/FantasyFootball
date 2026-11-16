from django.db import transaction
from rest_framework.exceptions import NotFound, ValidationError

from league.models import Player, Team

from .free_agency import current_entries
from .models import FantasyTeam, RosterEntry, WaiverClaim
from .rankings import FANTASY_POSITIONS
from .waiver_clock import waiver_clears_at

@transaction.atomic
def place_claim(team: FantasyTeam, player: Player | None = None, nfl_team: Team | None = None,
                drop_entry_id: int | None = None) -> WaiverClaim:
    """Claim a player who's on waivers, to be processed at the next 3 AM run. Goes to the end of the team's claim list."""
    league = team.league
    week, entries = current_entries(team)

    # 1. Same "can he be added at all?" check as add_player (draftable position, has an NFL team)
    if player is not None:
        if player.position not in FANTASY_POSITIONS or player.team_id is None:
            raise ValidationError({"player_id": f"{player} can't be added"})
        owned = RosterEntry.objects.filter(league=league, week=week, player=player)
    else:
        owned = RosterEntry.objects.filter(league=league, week=week, nfl_team=nfl_team)

    # 2. Owned by anyone this week? (same as add_player, naming the team)
    owner = owned.select_related("team").first()
    if owner is not None:
        raise ValidationError({"detail": f"{owner.team.name} already has him"})

    # 3. He must actually be on waivers (waiver_clears_at is None for a free agent)
    if waiver_clears_at(league, player, nfl_team) is None:
        raise ValidationError({"detail": "He's a free agent; add him instead."})

    # 4. No second pending claim on him from this team
    if team.claims.filter(player=player, nfl_team=nfl_team, status=WaiverClaim.Status.PENDING).exists():
        raise ValidationError({"detail": "You already have a claim on him."})

    # 5. The drop (optional) must be on this week's roster. Store WHO it is, not the row.
    drop = None
    if drop_entry_id is not None:
        drop = entries.get(drop_entry_id)
        if drop is None:
            raise ValidationError({"drop_entry_id": f"{drop_entry_id} is not on your roster this week"})

    # 6. Order: one after this team's last PENDING claim (0 when there are none)
    last = team.claims.filter(status=WaiverClaim.Status.PENDING).order_by("-order").values_list("order", flat=True).first() or 0
    return WaiverClaim.objects.create(
        team=team, player=player, nfl_team=nfl_team, order=last + 1,
        drop_player=drop.player if drop else None, drop_nfl_team=drop.nfl_team if drop else None,
    )


def cancel_claim(team: FantasyTeam, claim_id: int) -> None:
    """Delete one of this team's PENDING claims. 404 if it isn't one."""
    deleted, _ = team.claims.filter(status=WaiverClaim.Status.PENDING, id=claim_id).delete()
    if deleted == 0:
        raise NotFound("That isn't one of your pending claims.")
