from decimal import Decimal

from league.models import PlayerGameStats, TeamGameStats
from .models import FantasyTeam, RosterEntry

NOT_STARTING = {RosterEntry.Slot.BENCH, RosterEntry.Slot.IR}

def player_points(stats, rules) -> Decimal:
    """Fantasy points for one PlayerGameStats line under one league's ScoringRules."""
    points = Decimal("0")

    # Passing
    points += stats.pass_yards * rules.pass_yard
    points += stats.pass_tds * rules.pass_td
    points += stats.interceptions_thrown * rules.interception

    # Rushing
    points += stats.rush_yards * rules.rush_yard
    points += stats.rush_tds * rules.rush_td

    # Receiving
    points += stats.receptions * rules.reception
    points += stats.rec_yards * rules.rec_yard
    points += stats.rec_tds * rules.rec_td

    # Misc
    points += (stats.kick_return_tds + stats.punt_return_tds) * rules.return_td
    points += stats.two_point_conversions * rules.two_point
    points += stats.fumbles_lost * rules.fumble_lost

    # Kicking
    points += stats.fg_made_0_39 * rules.fg_0_39
    points += stats.fg_made_40_49 * rules.fg_40_49
    points += stats.fg_made_50_plus * rules.fg_50_plus

    points += (stats.fg_attempts - stats.fg_made) * rules.fg_missed
    points += (stats.xp_attempts - stats.xp_made) * rules.xp_missed
    points += stats.xp_made * rules.xp_made

    return points


def points_allowed_score(points_allowed: int, rules) -> Decimal:
    """What a D/ST's points-allowed bracket is worth: 0, 1-6, 7-13, 14-17, 18-27, 28-34, 35-45, 46+."""
    if points_allowed == 0:
        return rules.pa_0
    if points_allowed <= 6:
        return rules.pa_1_6
    if points_allowed <= 13:
        return rules.pa_7_13
    if points_allowed <= 17:
        return rules.pa_14_17
    if points_allowed <= 27:
        return rules.pa_18_27
    if points_allowed <= 34:
        return rules.pa_28_34
    if points_allowed <= 45:
        return rules.pa_35_45

    return rules.pa_46_plus


def dst_points(stats, rules) -> Decimal:
    """Fantasy points for one TeamGameStats (D/ST) line."""
    points = Decimal("0")
    points += stats.sacks * rules.dst_sack
    points += stats.interceptions * rules.dst_interception
    points += stats.fumble_recoveries * rules.dst_fumble_recovery
    points += stats.tds * rules.dst_td
    points += stats.safeties * rules.dst_safety
    points += stats.blocked_kicks * rules.dst_blocked_kick
    points += points_allowed_score(stats.points_allowed, rules)

    return points

def team_week_points(team: FantasyTeam, week: int) -> dict:
    """Every rostered row's points for `week`, plus the team's total (starters only)."""
    rules = team.league.scoring
    season = team.league.season
    entries = list(team.roster.filter(week=week).select_related("player__team", "nfl_team").order_by("id"))

    player_ids = [e.player_id for e in entries if e.player_id]
    player_lines = {s.player_id: s for s in PlayerGameStats.objects.filter(
        game__season=season, game__week=week, player_id__in=player_ids)}

    dst_ids = [e.nfl_team_id for e in entries if e.nfl_team_id]
    dst_lines = {s.team_id: s for s in TeamGameStats.objects.filter(
        game__season=season, game__week=week, team_id__in=dst_ids)}

    rows = []
    total = Decimal("0")
    for entry in entries:
        if entry.player_id:
            line = player_lines.get(entry.player_id)
            points = player_points(line, rules) if line else Decimal("0")
        else:
            line = dst_lines.get(entry.nfl_team_id)
            points = dst_points(line, rules) if line else Decimal("0")
        if entry.slot not in NOT_STARTING:
            total += points
        rows.append({"entry": entry, "points": points, "has_stats": line is not None})

    return {"week": week, "total": total, "rows": rows}