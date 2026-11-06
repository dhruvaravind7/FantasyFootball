import random
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import FantasyTeam, League
from .rankings import build_rankings

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

    # 2. Shrink (or grow) the size to exactly the number of teams
    league.size = len(teams)

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