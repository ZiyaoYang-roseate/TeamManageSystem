import unittest
from collections import Counter
from unittest.mock import patch

from decision_analysis import _fit_factor
from roster_recommendations import _recommend, _trade_plan


class DecisionAnalysisTests(unittest.TestCase):
    def test_fit_distinguishes_rebound_upgrade_from_missing_sample(self):
        baseline = (8, [{"metric": "rebounds", "ours": 38.0, "opponents": 43.0, "gap": 5.0}])
        teammate = {"sample": {"games": 4, "seconds": 4320, "rebounds": 10}}
        candidate = {"sample": {"games": 4, "seconds": 4320, "rebounds": 18}}

        improvement = _fit_factor("fit", baseline, [candidate], [teammate], "换出球员")
        self.assertEqual(improvement["title"], "增加篮板保护")
        self.assertIn("候选方案每 36 分钟 9.0，换出球员 5.0", improvement["fact"])

        missing = _fit_factor("fit", baseline, [{"sample": None}], [teammate], "同位置球员")
        self.assertEqual(missing["title"], "先补球员样本")
        self.assertIn("本队优先关注篮板", missing["fact"])
        self.assertNotIn("每 36 分钟", missing["fact"])

    def test_trade_value_explains_benefit_and_cost_in_the_same_package(self):
        base = {"games": 4, "seconds": 4320, "points": 30,
                "rebounds": 10, "assists": 10, "threes": 4, "turnovers": 4}
        outgoing = {"name": "A", "position": "SG", "sample": base}
        incoming = {"name": "B", "team_name": "Rival", "position": "PF",
                    "sample": {**base, "rebounds": 18, "assists": 4}}
        salary = {"outgoing": 10_000_000, "incoming": 7_000_000, "cap_space_after": 1_000_000}
        needs = [{"metric": "rebounds", "ours": 38, "opponents": 43}]
        result = _trade_plan({}, outgoing, incoming, "rebounds", salary,
                             Counter({"SG": 2, "PF": 1}), needs, 142_000_000, 140_000_000)["value"]
        self.assertIn("篮板保护", result["summary"])
        self.assertIn("弱侧卡位", result["usage"])
        self.assertIn("换入 9.0，换出 5.0", result["court"])
        self.assertIn("这是本队的短板", result["court"])
        self.assertIn("SG 只剩一名", result["rotation"])
        self.assertIn("节省 $3,000,000", result["budget"])
        self.assertIn("超帽 $2,000,000变为可用 $1,000,000", result["budget"])
        self.assertIn("回到奢侈税线以内", result["budget"])
        self.assertIn("助攻 5.0 → 2.0", result["tradeoff"])

    def test_model_preference_changes_the_on_court_role_with_its_own_evidence(self):
        stats = {"games": 4, "seconds": 4320, "points": 30,
                 "rebounds": 10, "assists": 6, "threes": 4, "turnovers": 4}
        outgoing = {"name": "A", "position": "PG", "sample": stats}
        incoming = {"name": "B", "team_name": "Rival", "position": "PG",
                    "sample": {**stats, "rebounds": 18, "assists": 12, "turnovers": 8}}
        salary = {"outgoing": 10_000_000, "incoming": 10_000_000, "cap_space_after": 1_000_000}
        proposal = _trade_plan({"team1_outgoing_player_ids": [10]}, outgoing, incoming, "rebounds", salary,
                               Counter({"PG": 2}), [], 142_000_000, 150_000_000)
        with (
            patch("roster_recommendations.configured_model", return_value="deepseek-flash"),
            patch("roster_recommendations.generate_content", return_value=(
                "model", {"items": [{"evidence_id": "option0", "action_id": "trade_creation"}]},
            )) as analysis,
        ):
            result = _recommend("trade_options", [proposal], 8, [], "")["plans"][0]["value"]
        self.assertIn("trade_creation", analysis.call_args.args[0]["evidence"][0]["actions"])
        self.assertIn("第二阵容", result["summary"])
        self.assertIn("帮助更多队友参与进攻", result["usage"])
        self.assertIn("历史失误也更高", result["usage"])
        self.assertIn("换入 6.0，换出 3.0", result["court"])
        self.assertIn("接下来三场", result["verify"])
