import unittest
import os
from datetime import date
from unittest.mock import patch

from starting_lineups import add_lineup_evidence
from analysis_model import _model_report, rules_report


class CandidateCursor:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def execute(self, sql, params):
        self.queries.append((sql, params))

    def fetchall(self):
        return self.rows.pop(0)


class StartingLineupTests(unittest.TestCase):
    def test_candidates_cover_positions_and_omit_unavailable_players(self):
        players = [
            {"id": index + 1, "name": f"P{index}", "position": position, "status": None, "injury_status": None}
            for index, position in enumerate(("PG", "SG", "SF", "PF", "C", "PG"))
        ]
        players[-1]["status"] = "rest"
        samples = [
            {"player_id": row["id"], "games": 5, "seconds": 6000, "points": 70,
             "assists": 20, "rebounds": 25, "threes": 7, "turnovers": 8} for row in players
        ]
        cursor = CandidateCursor([players, samples])
        game = {"id": 1, "status": "scheduled", "game_date": date(2026, 1, 1)}
        snapshot = {"evidence": [], "warnings": [], "zones": [], "kind": "scout"}
        add_lineup_evidence(cursor, game, 1, snapshot)
        self.assertEqual(len(snapshot["starting_lineups"]), 1)
        lineup = snapshot["starting_lineups"][0]
        self.assertEqual([row["id"] for row in lineup["players"]], [1, 2, 3, 4, 5])
        self.assertEqual(lineup["coverage"], 5)
        self.assertIn("g.game_date<%s", cursor.queries[1][0])
        self.assertIn(game["game_date"], cursor.queries[1][1])
        report = rules_report(snapshot["evidence"])
        self.assertEqual(report["items"][0]["evidence_id"], lineup["id"])

    def test_model_must_choose_a_lineup_when_one_is_available(self):
        snapshot = {
            "kind": "scout", "warnings": [], "starting_lineups": [{"id": "starters-1"}],
            "evidence": [
                {"id": "starters-1", "fact": "five players", "actions": ["starting_lineup"]},
                {"id": "trend", "fact": "record", "actions": ["trend"]},
            ],
        }
        with patch.dict(os.environ, {"NBA_AI_PROVIDER": "openai"}), patch("analysis_model._openai_text", return_value='{"items":[{"evidence_id":"trend","action_id":"trend"}]}'):
            with self.assertRaisesRegex(ValueError, "exactly one"):
                _model_report(snapshot, "test")

    def test_unavailable_roster_does_not_fabricate_a_five(self):
        rows = [{"id": 1, "name": "P", "position": "PG", "status": "injured", "injury_status": None}]
        cursor = CandidateCursor([rows])
        snapshot = {"evidence": [], "warnings": [], "zones": []}
        add_lineup_evidence(cursor, {"id": 1, "status": "scheduled"}, 1, snapshot)
        self.assertEqual(snapshot["starting_lineups"], [])
        self.assertEqual(len(snapshot["warnings"]), 1)
