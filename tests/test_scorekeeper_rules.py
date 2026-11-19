import unittest
from contextlib import contextmanager
from datetime import date
from unittest.mock import patch

import scorekeeper


class FakeCursor:
    def __init__(self, fetch_one_rows=None, fetch_all_rows=None):
        self.fetch_one_rows = list(fetch_one_rows or [])
        self.fetch_all_rows = list(fetch_all_rows or [])
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, sql, params=None):
        self.executions.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.fetch_one_rows.pop(0)

    def fetchall(self):
        return self.fetch_all_rows.pop(0)


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor

    def cursor(self):
        return self.fake_cursor


def fake_transaction_for(cursor):
    @contextmanager
    def fake_transaction():
        yield FakeConnection(cursor)

    return fake_transaction


class ScorekeeperRuleTests(unittest.TestCase):
    def test_game_state_includes_business_date_and_rosters(self):
        game = {
            "id": 7,
            "home_team_id": 1,
            "away_team_id": 2,
            "status": "live",
            "starters_initialized": 1,
            "game_date": date(2025, 12, 4),
            "business_date": date(2025, 12, 4),
        }
        with (
            patch.object(scorekeeper, "_scorekeeper_game_or_404", return_value=game),
            patch.object(scorekeeper, "_scorekeeper_roster", side_effect=[[1], [2]]),
            patch.object(scorekeeper, "_scorekeeper_events", return_value=[]),
        ):
            state = scorekeeper.game_state(7)

        self.assertTrue(state["can_score_today"])
        self.assertEqual(state["home_stats"], [1])
        self.assertEqual(state["away_stats"], [2])

    def test_only_current_business_date_can_be_scored(self):
        today_game = {
            "game_date": date(2025, 12, 4),
            "business_date": date(2025, 12, 4),
        }
        context = scorekeeper._require_current_scorekeeper_date(today_game)
        self.assertTrue(context["can_score_today"])

        with self.assertRaisesRegex(scorekeeper.ScorekeeperError, "超过当前比赛日"):
            scorekeeper._require_current_scorekeeper_date(
                {**today_game, "game_date": date(2025, 12, 3)}
            )
        with self.assertRaisesRegex(scorekeeper.ScorekeeperError, "不能提前计分"):
            scorekeeper._require_current_scorekeeper_date(
                {**today_game, "game_date": date(2025, 12, 5)}
            )

    def test_future_game_cannot_be_started(self):
        cursor = FakeCursor(
            fetch_one_rows=[
                {"home_team_id": 1, "away_team_id": 2, "status": "scheduled"},
                {"id": 1},
                {"id": 2},
                {
                    "id": 7,
                    "season": "2025-26",
                    "status": "scheduled",
                    "home_score": 0,
                    "away_score": 0,
                    "game_date": date(2025, 12, 5),
                    "business_date": date(2025, 12, 4),
                    "period": 1,
                    "seconds_remaining": 720,
                }
            ]
        )
        with patch.object(scorekeeper, "transaction", fake_transaction_for(cursor)):
            with self.assertRaisesRegex(scorekeeper.ScorekeeperError, "不能提前计分"):
                scorekeeper.start_game(7)

        self.assertFalse(any("UPDATE games" in sql for sql, _ in cursor.executions))

    def test_two_and_three_point_locations_follow_drawn_line(self):
        self.assertFalse(scorekeeper._is_three_point_location(0, 390))
        self.assertFalse(scorekeeper._is_three_point_location(200, 350))
        self.assertTrue(scorekeeper._is_three_point_location(230, 350))
        self.assertTrue(scorekeeper._is_three_point_location(0, 100))

        with self.assertRaisesRegex(scorekeeper.ScorekeeperError, "三分线外"):
            scorekeeper._validate_shot_location(3, 0, 390)
        with self.assertRaisesRegex(scorekeeper.ScorekeeperError, "三分线内"):
            scorekeeper._validate_shot_location(2, 0, 100)

    def test_overtime_clock_starts_after_regulation(self):
        regulation_end = scorekeeper._elapsed_game_seconds(4, 0)
        overtime_start = scorekeeper._elapsed_game_seconds(5, 300)
        self.assertEqual(regulation_end, overtime_start)
        self.assertEqual(scorekeeper._elapsed_game_seconds(5, 0), regulation_end + 300)

    def test_overtime_requires_tie_at_end_of_previous_period(self):
        tied_regulation_end = {
            "period": 4,
            "seconds_remaining": 0,
            "home_score": 100,
            "away_score": 100,
        }
        scorekeeper._validate_overtime_transition(tied_regulation_end, 5, 300)

        with self.assertRaisesRegex(scorekeeper.ScorekeeperError, "未打平"):
            scorekeeper._validate_overtime_transition(
                {**tied_regulation_end, "home_score": 101}, 5, 300
            )
        with self.assertRaisesRegex(scorekeeper.ScorekeeperError, "时间尚未结束"):
            scorekeeper._validate_overtime_transition(
                {**tied_regulation_end, "seconds_remaining": 1}, 5, 300
            )
        with self.assertRaisesRegex(scorekeeper.ScorekeeperError, "不能跳过节次"):
            scorekeeper._validate_overtime_transition(tied_regulation_end, 6, 300)
        with self.assertRaisesRegex(scorekeeper.ScorekeeperError, "5:00"):
            scorekeeper._validate_overtime_transition(tied_regulation_end, 5, 299)

    def test_first_quarter_lead_allows_finishing_and_preserves_clock(self):
        cursor = FakeCursor(fetch_one_rows=[{
            "id": 7, "game_date": date(2025, 12, 4), "business_date": date(2025, 12, 4),
            "status": "live", "starters_initialized": 1, "home_score": 5, "away_score": 2,
            "period": 1, "seconds_remaining": 480,
        }, {"id": 7, "status": "finished", "home_score": 5, "away_score": 2, "period": 1, "seconds_remaining": 480}])
        with patch.object(scorekeeper, "transaction", fake_transaction_for(cursor)), patch.object(scorekeeper, "invalidate_all"):
            result = scorekeeper.finish_game(7)
        self.assertEqual(result["game"]["period"], 1)
        self.assertEqual(result["game"]["seconds_remaining"], 480)
        self.assertTrue(any("UPDATE games SET status = 'finished'" in sql for sql, _ in cursor.executions))

    def test_tied_live_game_cannot_finish(self):
        cursor = FakeCursor(
            fetch_one_rows=[
                {
                    "id": 7,
                    "season": "2025-26",
                    "game_date": date(2025, 12, 4),
                    "business_date": date(2025, 12, 4),
                    "status": "live",
                    "starters_initialized": 1,
                    "home_score": 100,
                    "away_score": 100,
                    "period": 4,
                    "seconds_remaining": 0,
                }
            ]
        )
        with patch.object(scorekeeper, "transaction", fake_transaction_for(cursor)):
            with self.assertRaisesRegex(scorekeeper.ScorekeeperError, "比分相同"):
                scorekeeper.finish_game(7)

        self.assertFalse(any("UPDATE games" in sql for sql, _ in cursor.executions))

    def test_missed_free_throw_only_increases_attempts(self):
        cursor = FakeCursor(
            fetch_one_rows=[
                {
                    "id": 7,
                    "season": "2025-26",
                    "game_date": date(2025, 12, 4),
                    "business_date": date(2025, 12, 4),
                    "home_team_id": 1,
                    "away_team_id": 2,
                    "home_score": 80,
                    "away_score": 79,
                    "status": "live",
                    "starters_initialized": 1,
                    "period": 4,
                    "seconds_remaining": 12,
                },
                {"id": 9, "name": "Test Player", "team_id": 1},
                {"is_on_court": 1},
                {"home_score": 80, "away_score": 79},
            ],
            fetch_all_rows=[[{"team_id": 1, "active_count": 5}, {"team_id": 2, "active_count": 5}]],
        )

        with (
            patch.object(scorekeeper, "transaction", fake_transaction_for(cursor)),
            patch.object(scorekeeper, "invalidate_all"),
        ):
            result = scorekeeper.record_event(
                7,
                {"player_id": 9, "action": "add_free_throw_miss", "value": 0},
            )

        executed_sql = [sql for sql, _ in cursor.executions]
        self.assertIn("Free Throw Missed", result["message"])
        self.assertTrue(
            any(
                "SET free_throws_attempted = free_throws_attempted + 1" in sql
                for sql in executed_sql
            )
        )
        self.assertFalse(any("UPDATE games SET home_score" in sql for sql in executed_sql))
        self.assertFalse(any("INSERT INTO shot_attempts" in sql for sql in executed_sql))


if __name__ == "__main__":
    unittest.main()
