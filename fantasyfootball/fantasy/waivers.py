from collections import defaultdict
from datetime import datetime

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from league.models import Player, Team

from .free_agency import add_player, current_entries
from .models import FantasyTeam, League, RosterEntry, WaiverClaim
from .rankings import FANTASY_POSITIONS
from .waiver_clock import last_run, waiver_clears_at

def process_due_waivers(now: datetime | None = None) -> int:
    """Run the latest due 3 AM waiver run for every in-season league that hasn't had it. Returns how many claims won."""
    run_at = last_run(now or timezone.now())
    due = League.objects.filter(status=League.Status.IN_SEASON).filter(Q(last_waiver_run=None) | Q(last_waiver_run__lt=run_at))     # never run, OR last run before run_at
    return sum(process_league_claims(league, run_at) for league in due)

@transaction.atomic
def process_league_claims(league: League, run_at: datetime) -> int:
    """Process this league's ready claims for the `run_at` waiver run using rolling priority. Returns how many claims won."""
    # 1. Lock the league so two passes can't process the same run; skip if this run is already done
    league = League.objects.select_for_update().select_related("season").get(pk=league.pk)
    if league.last_waiver_run is not None and league.last_waiver_run >= run_at:
        return 0
    week = league.season.current_week
    from .bots import bot_moves
    bot_moves(league)

    # 2. Ready claims, grouped per team in the team's own order ({team id: [claim, claim, ...]})
    queues = defaultdict(list)
    pending = WaiverClaim.objects.filter(team__league=league, status=WaiverClaim.Status.PENDING).select_related("team__league__season", "player", "nfl_team")
    for claim in pending:                    # Meta ordering is (team, order), so each list is already in order
        if waiver_clears_at(league, claim.player, claim.nfl_team, run_at) is None:     # cleared by THIS run
            queues[claim.team_id].append(claim)

    # 3. Rolling priority: repeatedly take the highest team that still has a claim
    order = list(league.teams.order_by("waiver_priority"))
    won = 0
    while True:
        team = next((t for t in order if queues.get(t.id)), None)               # first team in `order` whose queue isn't empty
        if team is None:
            break
        claim = queues[team.id].pop(0)       # its top claim
        claim.processed_at = timezone.now()
        try:
            add_player(team, claim.player, claim.nfl_team,
                       drop_entry_id=drop_entry_id_for(claim, week), via_waivers=True)
        except ValidationError as error:
            claim.status, claim.note = WaiverClaim.Status.FAILED, first_error(error)
            claim.save(update_fields=["status", "note", "processed_at"])
            continue                         # same team, next claim. It keeps its spot.

        # Won: save it, the other teams' claims on him are LOST, and the winner goes to the bottom
        claim.status = WaiverClaim.Status.WON
        claim.save(update_fields=["status", "processed_at"])
        won += 1
        for claims in queues.values():
            for other in [c for c in claims if c.player_id == claim.player_id and c.nfl_team_id == claim.nfl_team_id]:
                claims.remove(other)
                other.status, other.note = WaiverClaim.Status.LOST, f"{team.name} claimed him first"
                other.processed_at = timezone.now()
                other.save(update_fields=["status", "note", "processed_at"])
        order.remove(team)
        order.append(team)

    # 4. Save the new order as priorities 1..n (like set_initial_priority), and mark this run done
    for position, team in enumerate(order, 1):
        team.waiver_priority = position
    league.teams.bulk_update(order, ["waiver_priority"])
    league.last_waiver_run = run_at
    league.save(update_fields=["last_waiver_run"])
    return won

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

def drop_entry_id_for(claim: WaiverClaim, week: int) -> int | None:
    """This week's roster row id for the claim's drop player/D/ST, None if the claim has no drop. 400 if he's gone."""
    if claim.drop_player_id is None and claim.drop_nfl_team_id is None:
        return None
    entry = claim.team.roster.filter(week=week, player=claim.drop_player, nfl_team=claim.drop_nfl_team).first()
    if entry is None:
        raise ValidationError({"detail": "The player you chose to drop is no longer on your roster."})
    return entry.id

def first_error(error: ValidationError) -> str:
    """The message inside a ValidationError like {"detail": "..."}, as text for a claim's note."""
    return str(next(iter(error.detail.values())))
