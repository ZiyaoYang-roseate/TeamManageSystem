import unittest
from datetime import date
from unittest.mock import patch

from player_directory import LEADER_METRICS, directory_data
from schedule_order import ordered_games
from team_planning import draft_recommendations
from trade_packages import package_plan, ranked_packages


class ProductUpdatesTests(unittest.TestCase):
    def test_each_board_descends_and_directory_keeps_unplayed_players(self):
        rows = []
        for player_id, games, points, assists in [(1, 2, 40, 2), (2, 4, 60, 24), (3, 0, None, None)]:
            rows.append({"id": player_id, "games": games, "name": f"球员{player_id}",
                         "team_name": "球队", "abbreviation": "T", "position": "PG",
                         "minutes": 40 if games else None,
                         **{key: (assists if key == "assists" else points) for key in LEADER_METRICS}})
        with patch("player_directory.fetch_all", return_value=rows):
            result = directory_data("2025-26", "assists", tab="all")
        self.assertEqual([p["id"] for p in result["rows"]], [2, 1, 3])
        self.assertEqual(result["boards"][0]["players"][0]["id"], 1)
        self.assertEqual(result["boards"][2]["players"][0]["id"], 2)
        self.assertEqual(len(result["boards"]), 6)
        self.assertIsNone(result["rows"][-1]["points"])

    def test_current_business_date_then_upcoming_then_completed(self):
        today = date(2025, 10, 1)
        fixtures = [
            (1, date(2025, 9, 1), "finished"), (2, date(2025, 10, 2), "scheduled"),
            (3, today, "finished"), (4, today, "scheduled"), (5, today, "live"),
            (6, date(2025, 9, 2), "finished"),
        ]
        games = [{"id": i, "game_date": day, "status": status} for i, day, status in fixtures]
        self.assertEqual([g["id"] for g in ordered_games(games, today)], [5, 4, 3, 2, 6, 1])

    def test_packages_allow_unequal_sizes_and_retain_both_teams_positions(self):
        def roster(team, start, assists):
            return [{"id": start+i, "team_id": team, "team_name": f"球队{team}",
                     "name": f"球员{start+i}", "position": ["PG", "SG", "SF", "PF", "C"][i%5],
                     "salary": 1_000_000, "sample": {"games": 10, "seconds": 21600,
                         "points": 180, "rebounds": 70, "assists": assists*10, "threes": 20, "turnovers": 20}}
                    for i in range(10)]
        rosters = {1: roster(1, 1, 3), 2: roster(2, 101, 6)}
        ranked = ranked_packages(1, rosters, {1: 10_000_000, 2: 10_000_000}, 100_000_000, [], set())
        self.assertTrue(ranked)
        self.assertTrue(any(len(row[1]["players"]) != len(row[2]["players"]) for row in ranked))
        score, outgoing, incoming, metric = next(row for row in ranked if len(row[1]["players"]) == 2 and len(row[2]["players"]) == 1)
        pending = {(2, outgoing["ids"], incoming["ids"])}
        reranked = ranked_packages(1, rosters, {1: 10_000_000, 2: 10_000_000}, 100_000_000, [], pending)
        self.assertFalse(any(row[1]["ids"] == outgoing["ids"] and row[2]["ids"] == incoming["ids"] for row in reranked))
        self.assertAlmostEqual(outgoing["rates"]["assists"], 3)
        plan = package_plan({}, outgoing, incoming, metric,
                            {"incoming": 1_000_000, "outgoing": 2_000_000, "cap_space_after": 91_000_000},
                            rosters[1], rosters[2], [], 10_000_000, 120_000_000)
        self.assertEqual(len(plan["profiles"]), 3)
        self.assertIn("未来数据的直接增量", plan["value"]["court"])
        self.assertIn("节省 $1,000,000", plan["value"]["budget"])

    def test_draft_shortlist_uses_external_profiles_and_model_choice(self):
        prospects = [{"id": i, "name": f"候选{i}", "position": "PG", "games": 20,
                      "minutes": 25, "assists": i+2, "rebounds": 5, "threes": 1, "turnovers": 2,
                      "profile": {"external": True, "source": "大学联赛", "fields": {"risk": "对抗", "role": "组织"}}}
                     for i in range(1, 5)]
        prospects.append({**prospects[0], "id": 5, "profile": {"external": False}})
        workspace = {"prospects": prospects, "needs": ["assists"], "priorities": ["助攻"],
                     "expiring": [{"position": "PG", "total": 1}]}
        with patch("analysis_model.configured_model", return_value="test"), patch(
            "analysis_model.generate_content", return_value=("model", {"items": [{"evidence_id": "prospect2", "action_id": "draft_creation"}]})
        ):
            result = draft_recommendations(workspace)
        self.assertEqual(result["source"], "model")
        self.assertEqual(result["candidates"][0]["id"], 2)
        self.assertEqual(len(result["candidates"]), 3)
        self.assertNotIn(5, [p["id"] for p in result["candidates"]])
        self.assertIn("到期", result["candidates"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
