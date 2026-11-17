import unittest
from datetime import datetime
from unittest.mock import patch

import app


class TransactionHistoryTests(unittest.TestCase):
    def test_trade_is_presented_from_the_selected_teams_perspective(self):
        trade_row = {
            "id": 1,
            "trade_date": datetime(2026, 9, 3, 10, 0),
            "team1_id": 10,
            "team2_id": 20,
            "team1_outgoing": "[101]",
            "team2_outgoing": "[201, 202]",
            "team1_city": "Los Angeles",
            "team1_name": "Lakers",
            "team1_abbreviation": "LAL",
            "team2_city": "Boston",
            "team2_name": "Celtics",
            "team2_abbreviation": "BOS",
        }
        players = [
            {"id": 101, "name": "Player A"},
            {"id": 201, "name": "Player B"},
            {"id": 202, "name": "Player C"},
        ]

        with patch.object(app, "fetch_all", side_effect=[[], [trade_row], players]):
            _, lakers_trades = app.team_transaction_history(10)

        self.assertEqual(lakers_trades[0]["sent_players"], ["Player A"])
        self.assertEqual(
            lakers_trades[0]["received_players"], ["Player B", "Player C"]
        )
        self.assertEqual(lakers_trades[0]["opponent_abbreviation"], "BOS")

        with patch.object(app, "fetch_all", side_effect=[[], [trade_row], players]):
            _, celtics_trades = app.team_transaction_history(20)

        self.assertEqual(
            celtics_trades[0]["sent_players"], ["Player B", "Player C"]
        )
        self.assertEqual(celtics_trades[0]["received_players"], ["Player A"])
        self.assertEqual(celtics_trades[0]["opponent_abbreviation"], "LAL")

    def test_invalid_trade_player_json_does_not_break_team_page(self):
        self.assertEqual(app._decode_history_player_ids("not-json"), [])
        self.assertEqual(app._decode_history_player_ids('{"id": 1}'), [])
        self.assertEqual(app._decode_history_player_ids('[1, "2", null]'), [1, 2])


if __name__ == "__main__":
    unittest.main()
