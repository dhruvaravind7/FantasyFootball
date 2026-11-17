from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.db.models import Q
from django.utils import timezone

from league.models import Game, Player, Team

from .models import League, WaiverHold

EASTERN = ZoneInfo("America/New_York")
RUN_HOUR = 3            # claims are processed daily at 3 AM Eastern
WAIVER_DAYS = 2         # a dropped player is on waivers at least this long

def start_hold(league: League, player: Player | None = None, nfl_team: Team | None = None) -> None:
    """Put a just-dropped player (or D/ST) on waivers until the first run at least WAIVER_DAYS from now."""
    WaiverHold.objects.update_or_create(
        league=league, player=player, nfl_team=nfl_team,
        defaults={"clears_at": next_run(timezone.now() + timedelta(days=WAIVER_DAYS))})

def waiver_clears_at(league: League, player: Player | None = None, nfl_team: Team | None = None,
                     now: datetime | None = None) -> datetime | None:
    """When this player/D/ST comes off waivers in `league`, or None if he's a free agent right now."""
    now = now or timezone.now()
    times = []

    # 1. Dropped recently? (filter(player=None) means "player is empty", so this works for a D/ST too)
    hold = league.waiver_holds.filter(player=player, nfl_team=nfl_team).first()
    if hold:
        times.append(hold.clears_at)

    # 2. Game-time: his NFL team's game this week has started -> on waivers until the run after kickoff
    team_id = player.team_id if player is not None else nfl_team.id      # the player's NFL team id, or the D/ST's own id
    game = Game.objects.filter(season=league.season, week=league.season.current_week).filter(Q(home_team=team_id)| Q(away_team=team_id)).first()   # home OR away
    if game and (game.kickoff <= now or game.status != Game.Status.SCHEDULED):
        times.append(next_run(game.kickoff))

    # 3. The later of the two, but only if it's still in the future
    latest = max(times, default=None)
    return latest if latest is not None and latest > now else None

def set_initial_priority(league: League) -> None:
    """Waiver priority 1..n in reverse draft order: the team that drafted last claims first."""
    teams = list(league.teams.order_by("-draft_position"))
    for position, team in enumerate(teams, 1):
        team.waiver_priority = position
    league.teams.bulk_update(teams, ["waiver_priority"])
    
def next_run(after: datetime) -> datetime:
    """The first daily waiver run (3 AM Eastern) strictly after `after`."""
    local = after.astimezone(EASTERN)
    run = local.replace(hour=RUN_HOUR, minute=0, second=0, microsecond=0)                                # today at RUN_HOUR:00:00.000000, in Eastern time
    if run <= local:                                  # today's run is at or before `after`, so it's tomorrow's
        run += timedelta(days=1)
    return run

def last_run(now: datetime) -> datetime:
    """The most recent 3 AM Eastern run at or before `now`."""
    local = now.astimezone(EASTERN)
    run = local.replace(hour=RUN_HOUR, minute=0, second=0, microsecond=0)
    if run > local:                                  # today's run hasn't happened yet, so the last one was yesterday's
        run -= timedelta(days=1)
    return run

def describe(when: datetime) -> str:
    """'Tue Nov 17, 3:00 AM ET': a waiver time for error messages."""
    return when.astimezone(EASTERN).strftime("%a %b %-d, %-I:%M %p ET")
