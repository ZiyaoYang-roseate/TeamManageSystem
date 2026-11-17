import io
import json
import os
import unittest
from datetime import date
from unittest.mock import patch

import analysis_model
import analysis_routes
import app
from game_analysis import build_review, build_scout, fingerprint


GAME = {
    "id": 20, "home_team_id": 1, "away_team_id": 2, "home_team_name": "主队",
    "away_team_name": "客队", "game_date": date(2025, 4, 2), "home_score": 5, "away_score": 3,
}
EVENTS = [
    {"id": 1, "period": 1, "seconds_remaining": 600, "score_home": 2, "score_away": 0},
    {"id": 2, "period": 4, "seconds_remaining": 1, "score_home": 2, "score_away": 3},
    {"id": 3, "period": 5, "seconds_remaining": 250, "score_home": 5, "score_away": 3},
]
SHOT = {
    "id": 81, "game_id": 20, "team_id": 1, "zone": "Mid-Range",
    "period": 1, "seconds_remaining": 600, "made": 1, "x": 12, "y": 240,
}


class GameAnalysisTests(unittest.TestCase):
    def test_review_sorts_events_and_tracks_overtime_and_perspective(self):
        snapshot = build_review(GAME, 2, list(reversed(EVENTS)), [], [])
        self.assertEqual(snapshot["score"], [3, 5])
        self.assertEqual(snapshot["lead_changes"], 2)
        self.assertEqual(snapshot["periods"][-1]["label"], "第 1 个加时")
        self.assertEqual(snapshot["periods"][-1]["margin"], -3)
        turning_point = next(item for item in snapshot["evidence"] if item["id"] == "lead-change")
        self.assertEqual(turning_point["refs"][0]["anchor"], "event-3")
        self.assertEqual(fingerprint(snapshot), fingerprint(build_review(GAME, 2, EVENTS, [], [])))

    def test_missing_and_inconsistent_events_keep_other_evidence(self):
        for events in ([], EVENTS[:-1]):
            with self.subTest(events=events):
                snapshot = build_review(GAME, 1, events, [], [SHOT])
                self.assertEqual(snapshot["timeline"], [])
                self.assertEqual(snapshot["score"], [5, 3])
                self.assertEqual(snapshot["zones"][0]["made"], 1)
                self.assertEqual(snapshot["zones"][0]["attempts"], 1)
                self.assertTrue(snapshot["warnings"])

    def test_impossible_scoring_jump_is_flagged(self):
        events = [{**EVENTS[-1], "score_home": 5, "score_away": 3}]
        snapshot = build_review(GAME, 1, events, [], [])
        self.assertFalse(snapshot["periods"])
        self.assertIn("累计比分", snapshot["warnings"][0])

    def test_changed_score_produces_a_new_evidence_version(self):
        original = build_review(GAME, 1, EVENTS, [], [])
        corrected = build_review({**GAME, "home_score": 6}, 1, EVENTS, [], [])
        self.assertNotEqual(fingerprint(original), fingerprint(corrected))

    def test_review_recommends_a_turnover_drill_when_box_score_supports_it(self):
        stat = {"id": 30, "game_id": 20, "team_id": 1, "player_id": 12,
                "player_name": "主队球员", "points": 5, "field_goals_attempted": 0, "turnovers": 5}
        opponent = {**stat, "id": 31, "team_id": 2, "player_id": 13, "points": 3, "turnovers": 1}
        report = build_review(GAME, 1, EVENTS, [stat, opponent], [])
        advice = next(item for item in report["evidence"] if item["id"] == "turnovers")
        self.assertIn("本队 5，对手 1", advice["fact"])
        self.assertIn("出球练习", advice["recommendation"])
        self.assertEqual(advice["refs"][0]["anchor"], "box-score")
        self.assertNotIn("turnovers", {item["id"] for item in build_review(GAME, 2, EVENTS, [stat, opponent], [])["evidence"]})

    def test_scout_uses_opponent_perspective_and_actual_sample_size(self):
        previous = {**GAME, "id": 10, "home_team_id": 2, "away_team_id": 1, "game_date": date(2025, 3, 30)}
        snapshot = build_scout(GAME, 1, 2, [previous], [], [{**SHOT, "game_id": 10, "team_id": 2}])
        self.assertEqual(snapshot["subject"], "客队")
        self.assertEqual(snapshot["samples"][0]["ours"], 5)
        self.assertIn("1 场", snapshot["warnings"][0])
        self.assertEqual(snapshot["zones"][0]["attempts"], 1)

    def test_model_response_and_relational_validation(self):
        snapshot = build_review(GAME, 1, EVENTS, [], [])
        choice = {"items": [{"evidence_id": "lead-change", "action_id": "swing"}]}
        response = {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(choice)}]}],
        }
        with (
            patch.dict(os.environ, {"NBA_AI_PROVIDER": "openai", "OPENAI_API_KEY": "unit-test"}),
            patch.object(analysis_model, "urlopen", return_value=io.BytesIO(json.dumps(response).encode())) as call,
        ):
            self.assertEqual(analysis_model.generate_content(snapshot, "test-model"), ("model", choice))
        self.assertFalse(json.loads(call.call_args.args[0].data)["store"])
        invalid = {"items": [{"evidence_id": "lead-change", "action_id": "shot_selection"}]}
        with self.assertRaises(ValueError):
            analysis_model.validate_report(invalid, snapshot["evidence"])

    def test_deepseek_accepts_valid_json_and_falls_back_for_unknown_evidence(self):
        snapshot = {
            "kind": "signing", "warnings": [],
            "evidence": [{
                "id": "offer", "fact": "Private salary amount",
                "model_fact": "Check the budget", "actions": ["offer_terms"],
            }],
        }
        choice = {"items": [{"evidence_id": "offer", "action_id": "offer_terms"}]}
        with patch.dict(os.environ, {"NBA_AI_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "unit-test", "NBA_AI_MODEL": ""}):
            model = analysis_model.configured_model()
            self.assertEqual(model, "deepseek-flash")
            for result, expected in ((choice, "model"), ({"items": [{"evidence_id": "unknown", "action_id": "offer_terms"}]}, "rules")):
                reply = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}]}
                with patch.object(analysis_model, "urlopen", return_value=io.BytesIO(json.dumps(reply).encode())) as call:
                    self.assertEqual(analysis_model.generate_content(snapshot, model), (expected, choice))
                    request = call.call_args.args[0]
                    self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
                    self.assertEqual(json.loads(request.data)["response_format"], {"type": "json_object"})
                    self.assertIn(b"Check the budget", request.data)
                    self.assertNotIn(b"Private salary amount", request.data)

    def test_model_timeout_falls_back_to_supported_evidence(self):
        snapshot = build_review(GAME, 1, EVENTS, [], [])
        with patch.object(analysis_model, "_model_report", side_effect=TimeoutError):
            source, content = analysis_model.generate_content(snapshot, "test-model")
        self.assertEqual(source, "rules")
        self.assertEqual(content, analysis_model.validate_report(content, snapshot["evidence"]))

    def test_started_game_can_open_pregame_analysis_without_a_saved_report(self):
        client = app.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = 1
        user = {"id": 1, "role": "management", "team_id": 1, "display_name": "Manager"}
        for status in ("live", "finished"):
            with (
                self.subTest(status=status),
                patch.object(app, "get_user_by_id", return_value=user),
                patch.object(analysis_routes, "transaction"),
                patch.object(analysis_routes, "get_game", return_value={**GAME, "status": status, "starters_initialized": 1}),
                patch.object(analysis_routes, "_history", return_value=[]),
                patch.object(analysis_routes, "load_evidence", return_value=build_scout(GAME, 1, 2, [], [], [])) as load_evidence,
            ):
                response = client.get("/games/20/analysis/scout")
                self.assertEqual(response.status_code, 200)
                self.assertIn("之前对手的全部完赛记录", response.get_data(as_text=True))
                self.assertNotIn('id="analysisTrend"', response.get_data(as_text=True))
                load_evidence.assert_called_once_with(20, 1, "scout")

    def test_manager_can_read_other_teams_but_not_publish_their_starters(self):
        with app.app.test_request_context("/games/20/analysis/review?team=2"):
            from flask import g

            g.user = {"role": "management", "team_id": 3}
            with patch.object(analysis_routes, "transaction"), patch.object(analysis_routes, "get_game", return_value=GAME):
                _, perspective, _ = analysis_routes._context(20, "review")
                self.assertEqual(perspective, 2)
                self.assertEqual(analysis_routes._context(20, saved_team=2)[1], 2)
                with self.assertRaisesRegex(Exception, "403"):
                    analysis_routes._context(20, "scout")
                with self.assertRaisesRegex(Exception, "403"):
                    analysis_routes._context(20, "scout", saved_team=2)
            with (
                app.app.test_request_context("/games/20/starters", method="POST", data={"team_id": 2}),
                patch.object(analysis_routes, "_context", return_value=(GAME, 2, {1: "主队", 2: "客队"})),
                patch.object(analysis_routes, "transaction") as tx,
            ):
                g.user = {"role": "management", "team_id": 3}
                with self.assertRaisesRegex(Exception, "403"):
                    analysis_routes.publish_starters(20)
                tx.assert_not_called()
