import random
import logging
from collections import Counter
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .matchups import build_schedule
from .models import DraftPick, DraftQueueEntry, FantasyTeam, League, RosterEntry
from .rankings import FANTASY_POSITIONS, build_rankings

logger = logging.getLogger(__name__)

POSITION_LIMITS = {"QB": 4, "RB": 8, "WR": 8, "TE": 3, "PK": 3, "DST": 3}
FLEX_POSITIONS = {"RB", "WR", "TE"}
SLOT_FOR_POSITION = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "PK": "K", "DST": "DST"}    # roster slot names


def starter_slots(league) -> dict:
    """How many starting spots each position has in this league (not counting FLEX)."""
    return {"QB": league.qb_slots, "RB": league.rb_slots, "WR": league.wr_slots, "TE": league.te_slots, "PK": league.k_slots, "DST": league.dst_slots}


def pick_position(pick) -> str:
    """'DST' for a D/ST pick, otherwise the player's position."""
    return "DST" if pick.nfl_team_id else pick.player.position


def team_on_clock(league):
    """The FantasyTeam whose turn it is."""
    position = draft_position_on_clock(league.current_pick, league.size)
    return league.teams.get(draft_position=position)


def position_counts(team) -> Counter:
    """{"QB": 1, "RB": 3, ...} for this team's picks so far."""
    return Counter(pick_position(p) for p in team.picks.select_related("player"))

def fills_starter(position: str, counts: Counter, league) -> bool:
    """Would one more `position` fill an empty starting spot, either his own position's or FLEX?"""
    slots = starter_slots(league)
    if counts[position] < slots[position]:
        return True
    if position in FLEX_POSITIONS:

        flex_used = sum(max(0, counts[p] - slots[p]) for p in FLEX_POSITIONS)
        return True if flex_used < league.flex_slots else False
    return False

def draft_position_on_clock(pick: int, teams: int) -> int:
    """Which draft position (1-based) makes overall pick `pick` in a snake draft with `teams` teams."""
    index = (pick - 1) % teams                  # 0-based spot within the round
    round_index = (pick - 1) // teams           # 0 for round 1, 1 for round 2, ...
    if round_index % 2 == 1:                    # rounds 2, 4, 6... go backwards
        index = teams - 1 - index               # reversed: the last position picks first
    return index + 1

@transaction.atomic
def start_draft(league: League) -> None:
    """Fill with bots if needed, shrink the size to fit, pick a random order, rank players, and start the first clock."""
    if league.status != League.Status.SETUP:
        raise ValidationError({"detail": "This league has already drafted."})

    # 1. Bots: fewer than 4 teams → add bots up to 4. An odd number → add 1 bot. (Even and ≥ 4 → none.)
    teams = list(league.teams.all())
    if len(teams) < 4:
        bots_needed = 4 - len(teams)
    elif len(teams) % 2 == 1:
        bots_needed = 1
    else:
        bots_needed = 0

    # Skip any "Bot N" name a person already took, so the unique team-name constraint never fails
    n = 0
    while bots_needed > 0:
        n += 1
        if league.teams.filter(name=f"Bot {n}").exists():
            continue
        teams.append(league.teams.create(name=f"Bot {n}"))      # owner left empty = a bot
        bots_needed -= 1

    # 2. Shrink (or grow) the size to exactly the number of teams. A 6-team bracket can't fit fewer than 6 teams.
    league.size = len(teams)
    if league.playoff_teams > league.size:
        league.playoff_teams = 4

    # 3. Random order: shuffle, then give each team its position 1..n
    random.shuffle(teams)
    for position, team in enumerate(teams, start=1):
        team.draft_position = position
    FantasyTeam.objects.bulk_update(teams, ["draft_position"])

    # 4. This league's rankings, for autopick (Step 8c)
    build_rankings(league)

    # 5. Go live: status DRAFTING, pick 1 on the clock, deadline = now + pick_seconds
    league.status = League.Status.DRAFTING
    league.current_pick = 1
    league.pick_deadline = timezone.now() + timedelta(seconds=league.pick_seconds)
    league.save()

@transaction.atomic
def make_pick(league, team, player=None, nfl_team=None, auto=False):
    """Make the current pick for `team`. Every pick goes through here: a person, the queue, or autopick."""
    league = League.objects.select_for_update().get(pk=league.pk)

    # 1. The league must be drafting, and it must be this team's turn
    if league.status != League.Status.DRAFTING:
        raise ValidationError({"detail": "The league is not drafting right now"})
    if league.paused_seconds_left is not None:
        raise ValidationError({"detail": "The draft is paused."})
    if team != team_on_clock(league):
        raise ValidationError({"detail": "It is not your turn to draft"})

    # 2. Exactly one of player / nfl_team. (player is None) == (nfl_team is None) is True when both or neither were given.
    if (player is None) == (nfl_team is None):
        raise ValidationError({"detail": "Pick exactly one player or D/ST."})
    
    # 3. Available and allowed
    if player is not None:
        if player.position not in FANTASY_POSITIONS or player.team_id is None:
            raise ValidationError({"detail": "That player can't be drafted."})
        if DraftPick.objects.filter(league=league, player=player).exists():
            raise ValidationError({"detail": "Already drafted"})
        position = player.position
    else:
        if DraftPick.objects.filter(league=league, nfl_team=nfl_team).exists():
            raise ValidationError({"detail": "Already drafted"})
        position = "DST"

    # 4. Position limit
    if position_counts(team)[position] >= POSITION_LIMITS[position]:
        raise ValidationError({"detail": f"You already have the most {position}s allowed ({POSITION_LIMITS[position]})."})

    # 5. Save the pick (number = the pick on the clock)
    pick = DraftPick.objects.create(league=league, number=league.current_pick, team=team, player=player, nfl_team=nfl_team, auto=auto)

    # 6. He's gone, so take him off EVERY team's queue in this league
    #    (team__league=league filters queue entries across the team link)
    if player is not None:
        DraftQueueEntry.objects.filter(team__league=league, player=player).delete()
    else:
        DraftQueueEntry.objects.filter(team__league=league, nfl_team=nfl_team).delete()

    # 7. Last pick → finish. Otherwise the next pick goes on the clock with a fresh deadline.
    if league.current_pick >= league.size * league.roster_size:
        finish_draft(league)
    else:
        league.current_pick += 1
        league.pick_deadline = timezone.now() + timedelta(seconds=league.pick_seconds)
        league.save(update_fields=["current_pick", "pick_deadline"])
    return pick

def autopick(league, team):
    """Pick for `team`: its queue first, then the best player for an empty starting spot, then best available."""
    counts = position_counts(team)

    # 1. Queue
    for entry in team.queue.select_related("player"):
        position = "DST" if entry.nfl_team_id else entry.player.position
        if counts[position] < POSITION_LIMITS[position]:
            return make_pick(league, team, player=entry.player, nfl_team=entry.nfl_team, auto=True)

    # Everyone not drafted yet, best rank first
    taken_players = set(league.picks.exclude(player=None).values_list("player_id", flat=True))
    taken_teams = set(league.picks.exclude(nfl_team=None).values_list("nfl_team_id", flat=True))
    available = [
        r for r in league.rankings.select_related("player", "nfl_team")
        if (r.player_id not in taken_players if r.player_id else r.nfl_team_id not in taken_teams)
    ]

    # 2. Starters
    for r in available:
        if fills_starter(r.position, counts, league):
            return make_pick(league, team, player=r.player, nfl_team=r.nfl_team, auto=True)

    # 3. Best available (never a 2nd K or D/ST)
    for r in available:
        if r.position in ("PK", "DST") and counts[r.position] > 0:
            continue
        if counts[r.position] < POSITION_LIMITS[r.position]:
            return make_pick(league, team, player=r.player, nfl_team=r.nfl_team, auto=True)

    raise ValidationError({"detail": "No player left to autopick."})

def finish_draft(league):
    """Fill every roster from the draft (starters first, in draft order) and start the season."""
    week = league.season.current_week or 1
    entries = []
    for team in league.teams.all():
        counts = Counter()
        slots = starter_slots(league)
        flex_left = league.flex_slots
        for pick in team.picks.select_related("player").order_by("number"):
            position = pick_position(pick)
            if counts[position] < slots[position]:
                slot = SLOT_FOR_POSITION[position]
            elif position in FLEX_POSITIONS and flex_left > 0:
                slot = RosterEntry.Slot.FLEX
                flex_left -= 1
            else:
                slot = RosterEntry.Slot.BENCH
            counts[position] += 1
            entries.append(RosterEntry(team=team, league=league, week=week,
                                       player_id=pick.player_id, nfl_team_id=pick.nfl_team_id, slot=slot))

    RosterEntry.objects.bulk_create(entries)
    league.status = League.Status.IN_SEASON
    league.current_pick, league.pick_deadline = None, None
    league.save(update_fields=["status", "current_pick", "pick_deadline"])
    build_schedule(league)

@transaction.atomic
def pause_draft(league) -> None:
    """Stop the clock, remembering how much time the current pick had left."""
    league = League.objects.select_for_update().get(pk=league.pk)
    if league.status != League.Status.DRAFTING or league.paused_seconds_left is not None:
        raise ValidationError({"detail": "The draft isn't running."})
    
    league.paused_seconds_left = max(0, int((league.pick_deadline - timezone.now()).total_seconds()))
    league.pick_deadline = None
    league.save(update_fields=["paused_seconds_left", "pick_deadline"])


@transaction.atomic
def resume_draft(league) -> None:
    """Restart the clock with the time it had when paused."""
    league = League.objects.select_for_update().get(pk=league.pk)
    if league.paused_seconds_left is None:
        raise ValidationError({"detail": "The draft isn't paused."})
    league.pick_deadline = timezone.now() + timedelta(seconds=league.paused_seconds_left)
    league.paused_seconds_left = None
    league.save(update_fields=["paused_seconds_left", "pick_deadline"])


@transaction.atomic
def undo_last_pick(league) -> None:
    """Take back the most recent pick. That team goes back on the clock with a full timer."""
    league = League.objects.select_for_update().get(pk=league.pk)
    if league.status != League.Status.DRAFTING or league.current_pick == 1:
        raise ValidationError({"detail": "There's no pick to undo."})
    league.picks.get(number=league.current_pick-1).delete()      # the pick BEFORE the one on the clock
    league.current_pick -= 1
    # A paused draft stays paused, but with a full timer waiting.
    # A running draft gets a fresh deadline.
    if league.paused_seconds_left is not None:  # Paused
        league.paused_seconds_left = league.pick_seconds
    else:
        league.pick_deadline = timezone.now() + timedelta(seconds=league.pick_seconds)
    league.save(update_fields=["current_pick", "pick_deadline", "paused_seconds_left"])
    
def run_league(league) -> int:
    """Make every pick that's due in this league right now. Returns how many picks it made."""
    picks = 0
    while True:
        league.refresh_from_db()                              # make_pick changed it, so read the new state
        if league.status != League.Status.DRAFTING or league.paused_seconds_left is not None:
            return picks
        team = team_on_clock(league)
        if team.owner_id is None or team.autodraft:           # a bot, or a team on autodraft: pick now
            autopick(league, team)
        elif timezone.now() >= league.pick_deadline:          # a human's clock ran out
            team.autodraft = True
            team.save(update_fields=["autodraft"])
            autopick(league, team)
        else:
            return picks                                      # a human is on the clock with time left
        picks += 1

def poll() -> int:
    """One pass of the runner: start due drafts, then move every live draft forward. Returns picks made."""
    # 1. Start every league in SETUP whose draft_time has passed (draft_time__lte=timezone.now())
    for league in League.objects.filter(draft_time__lte=timezone.now(), status=League.Status.SETUP):
        try:
            start_draft(league)
        except ValidationError as e:
            logger.warning("Couldn't start the draft for %s: %s", league, e)
    # 2. Every DRAFTING league: run_league, and add up the picks.
    #    Same try/except, so one stuck league doesn't stop the others.
    total = 0
    for league in League.objects.filter(status=League.Status.DRAFTING):
        try:
            total += run_league(league=league)
        except ValidationError as e:
            logger.warning("Couldn't run the draft for %s: %s", league, e)
    return total