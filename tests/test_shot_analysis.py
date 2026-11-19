import unittest

from shot_analysis import annotate_zones


class ShotAnalysisTests(unittest.TestCase):
    def test_any_attempt_compares_efficiency_but_missing_benchmark_remains_unavailable(self):
        zones = [
            {"attempts": 1, "league_attempts": 100, "diff": 55},
            {"attempts": 20, "league_attempts": 0, "diff": 55},
            {"attempts": 0, "league_attempts": 100, "diff": -45},
            {"attempts": 10, "league_attempts": 40, "diff": -5},
        ]
        self.assertEqual(annotate_zones(zones), 31)
        self.assertEqual([row["efficiency_band"] for row in zones], ["higher", "unavailable", "empty", "lower"])
        self.assertEqual([row["share"] for row in zones], [3.2, 64.5, 0, 32.3])
