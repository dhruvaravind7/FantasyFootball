"""Scoring math. These need no database: unsaved model objects carry their field defaults."""
from decimal import Decimal

import pytest

from fantasy.models import ScoringRules
from fantasy.scoring import dst_points, player_points, points_allowed_score
from league.models import PlayerGameStats, TeamGameStats


@pytest.mark.parametrize("stats, expected", [
    ({"pass_yards": 300, "pass_tds": 2, "interceptions_thrown": 1}, Decimal("18")),     # 12 + 8 - 2
    ({"receptions": 6, "rec_yards": 90, "rec_tds": 1}, Decimal("21")),                  # 6 + 9 + 6 (PPR)
    ({"rush_yards": 105, "rush_tds": 1, "fumbles_lost": 1}, Decimal("14.5")),            # 10.5 + 6 - 2
    ({"fg_attempts": 4, "fg_made": 3, "fg_made_0_39": 2, "fg_made_50_plus": 1,
      "xp_attempts": 3, "xp_made": 3}, Decimal("13")),                                  # 6 + 5 - 1 miss + 3
    ({}, Decimal("0")),                                                                  # played, did nothing
])
def test_player_points(stats: dict, expected: Decimal):
    """One stat line under the default (PPR) rules."""
    assert player_points(PlayerGameStats(**stats), ScoringRules()) == expected


def test_half_ppr_changes_only_receptions():
    """Half-PPR: the same 6-catch line is worth 3 points less."""
    rules = ScoringRules(reception=ScoringRules.RECEPTION_POINTS[ScoringRules.Preset.HALF_PPR])
    assert player_points(PlayerGameStats(receptions=6, rec_yards=90, rec_tds=1), rules) == Decimal("18")


@pytest.mark.parametrize("allowed, expected", [
    (0, 5), (1, 4), (6, 4), (7, 3), (13, 3), (14, 1), (17, 1),
    (18, 0), (27, 0), (28, -1), (34, -1), (35, -3), (45, -3), (46, -5),
])
def test_points_allowed_brackets(allowed: int, expected: int):
    """Both edges of every points-allowed bracket."""
    assert points_allowed_score(allowed, ScoringRules()) == expected


def test_dst_points():
    """3 sacks + 1 INT + 1 TD + 10 points allowed = 3 + 2 + 6 + 3."""
    line = TeamGameStats(sacks=3, interceptions=1, tds=1, points_allowed=10)
    assert dst_points(line, ScoringRules()) == Decimal("14")
