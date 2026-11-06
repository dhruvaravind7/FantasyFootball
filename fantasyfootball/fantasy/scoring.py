from decimal import Decimal


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