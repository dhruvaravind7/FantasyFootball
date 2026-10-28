from django.test import SimpleTestCase

from .engine import GameState, PlayOutcome, next_state


def state(down=1, distance=10, yardline_100=75):
    """Helper so each test only spells out what it cares about."""
    return GameState(offense="SEA", defense="SF", down=down, distance=distance, yardline_100=yardline_100)


class DownAndDistanceTests(SimpleTestCase):
    def test_short_gain_advances_down(self):
        result = next_state(state(down=1, distance=10, yardline_100=75), PlayOutcome(yards_gained=4))
        self.assertEqual(result.next_state.down, 2)
        self.assertEqual(result.next_state.distance, 6)
        self.assertEqual(result.next_state.yardline_100, 71)

    def test_gain_past_sticks_is_first_down(self):
        result = next_state(state(), PlayOutcome(yards_gained=11))
        self.assertEqual(result.next_state.down, 1)
        self.assertEqual(result.next_state.distance, 10)
        self.assertEqual(result.next_state.yardline_100, 64)
        
    def test_loss_of_yards(self):
        result = next_state(state(), PlayOutcome(-3))
        self.assertEqual(result.next_state.down, 2)
        self.assertEqual(result.next_state.distance, 13)
        self.assertEqual(result.next_state.yardline_100, 78)
    
    def test_gain_exactly_to_the_sticks_is_first_down(self):
        result = next_state(state(down=3, distance=4, yardline_100=40), PlayOutcome(yards_gained=4))
        self.assertEqual(result.next_state.down, 1)
        self.assertEqual(result.next_state.distance, 10)
        self.assertEqual(result.next_state.yardline_100, 36)

    def test_first_down_inside_the_ten_is_goal_to_go(self):
        # 1st & 10 at the 17, gain 10 -> 1st & goal at the 7 (distance 7, not 10)
        result = next_state(state(yardline_100=17), PlayOutcome(yards_gained=10))
        self.assertEqual(result.next_state.down, 1)
        self.assertEqual(result.next_state.distance, 7)
        self.assertEqual(result.next_state.yardline_100, 7)

    def test_short_gain_on_goal_to_go_keeps_distance_equal_to_yardline(self):
        # 1st & goal at the 7, gain 3 -> 2nd & goal at the 4
        result = next_state(state(down=1, distance=7, yardline_100=7), PlayOutcome(yards_gained=3))
        self.assertEqual(result.next_state.down, 2)
        self.assertEqual(result.next_state.distance, 4)
        self.assertEqual(result.next_state.yardline_100, 4)

    def test_touchdown_from_exact_yardage(self):
        result = next_state(state(yardline_100=12), PlayOutcome(yards_gained=12))
        self.assertIsNone(result.next_state)
        self.assertEqual(result.points, 6)
        self.assertEqual(result.event, "TOUCHDOWN")

    def test_safety(self):
        # Sacked at the 3 for a loss of 5 -> tackled in own end zone.
        # Points are from the OFFENSE's point of view, so a safety is -2.
        result = next_state(state(down=2, distance=10, yardline_100=97), PlayOutcome(yards_gained=-5))
        self.assertIsNone(result.next_state)
        self.assertEqual(result.points, -2)
        self.assertEqual(result.event, "SAFETY")

    def test_fourth_down_conversion_keeps_possession(self):
        result = next_state(state(down=4, distance=2, yardline_100=45), PlayOutcome(yards_gained=3))
        self.assertEqual(result.next_state.offense, "SEA")
        self.assertEqual(result.next_state.down, 1)
        self.assertEqual(result.next_state.yardline_100, 42)
        self.assertEqual(result.event, "")


class PossessionChangeTests(SimpleTestCase):
    def test_fourth_down_failure_flips_possession(self):
        # 4th & 5 at SF's 40, gain 2 -> stopped at SF's 38.
        # SF takes over 62 yards from SEA's end zone: 1st & 10 at yardline_100 = 62.
        result = next_state(state(down=4, distance=5, yardline_100=40), PlayOutcome(yards_gained=2))
        self.assertEqual(result.event, "DOWNS")
        self.assertEqual(result.points, 0)
        self.assertEqual(result.next_state.offense, "SF")
        self.assertEqual(result.next_state.defense, "SEA")
        self.assertEqual(result.next_state.down, 1)
        self.assertEqual(result.next_state.distance, 10)
        self.assertEqual(result.next_state.yardline_100, 62)

    def test_turnover_on_downs_deep_in_own_territory_is_goal_to_go(self):
        # 4th & 1 at SEA's own 5 (yardline_100 = 95), no gain -> SF ball at SEA's 5: 1st & goal.
        result = next_state(state(down=4, distance=1, yardline_100=95), PlayOutcome(yards_gained=0))
        self.assertEqual(result.next_state.offense, "SF")
        self.assertEqual(result.next_state.yardline_100, 5)
        self.assertEqual(result.next_state.distance, 5)

    def test_interception_flips_possession(self):
        # Pass from SEA's 30 (yardline_100 = 70), picked off 20 yards downfield at
        # yardline_100 = 50, returned 15 yards -> SF ball at yardline_100 = 35.
        result = next_state(
            state(down=2, distance=6, yardline_100=70),
            PlayOutcome(yards_gained=20, turnover=True, return_yards=15),
        )
        self.assertEqual(result.event, "TURNOVER")
        self.assertEqual(result.points, 0)
        self.assertEqual(result.next_state.offense, "SF")
        self.assertEqual(result.next_state.defense, "SEA")
        self.assertEqual(result.next_state.down, 1)
        self.assertEqual(result.next_state.distance, 10)
        self.assertEqual(result.next_state.yardline_100, 35)

    def test_fumble_with_no_return(self):
        # Fumble lost at SF's 30 with no return -> SF ball at their own 30 (yardline_100 = 70).
        result = next_state(state(yardline_100=30), PlayOutcome(yards_gained=0, turnover=True))
        self.assertEqual(result.next_state.offense, "SF")
        self.assertEqual(result.next_state.yardline_100, 70)

    def test_pick_six(self):
        # Intercepted at yardline_100 = 40, returned 60 yards for a touchdown.
        # The DEFENSE scored, so from the offense's point of view that's -6.
        result = next_state(
            state(yardline_100=60),
            PlayOutcome(yards_gained=20, turnover=True, return_yards=60),
        )
        self.assertIsNone(result.next_state)
        self.assertEqual(result.points, -6)