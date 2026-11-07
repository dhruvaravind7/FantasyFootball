from .models import League, RosterEntry


def ensure_week(team, week: int) -> None:
    """Make sure `team` has roster rows for `week`, copying them from its last earlier week if needed."""
    if team.roster.filter(week=week).exists():
        return

    last_week = team.roster.filter(week__lt=week).order_by("-week").values_list("week", flat=True).first()
    if last_week is None:
        return      # a brand-new team, so nothing to copy

    RosterEntry.objects.bulk_create([
        RosterEntry(team=team, league=team.league, week=week,
                    player_id=entry.player_id, nfl_team_id=entry.nfl_team_id, slot=entry.slot)
        for entry in team.roster.filter(week=last_week)
    ])


def ensure_league_week(league: League, week: int) -> None:
    """ensure_week for every team in the league, using one query to skip the teams that already have rows."""
    have = set(RosterEntry.objects.filter(league=league, week=week).values_list("team_id", flat=True))
    for team in league.teams.all():
        if team.id not in have:
            ensure_week(team, week)
