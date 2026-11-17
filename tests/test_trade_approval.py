import unittest
from contextlib import contextmanager
from unittest.mock import patch

from flask import g
from werkzeug.exceptions import Forbidden

import app


class FakeCursor:
    def __init__(self, proposal):
        self.proposal = proposal
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, sql, params=None):
        self.executions.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.proposal


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


class TradeApprovalTests(unittest.TestCase):
    def test_validation_always_uses_logged_in_management_team(self):
        validation = {"is_valid": True, "reasons": []}
        with app.app.test_request_context(
            json={"team1_id": 999, "team2_id": 20}
        ):
            g.user = {"id": 1, "role": "management", "team_id": 10}
            with patch.object(app, "validate_trade", return_value=validation) as mocked:
                response = app.validate_trade_api()

        self.assertEqual(mocked.call_args.args[0]["team1_id"], 10)
        self.assertEqual(response.get_json(), validation)

    def test_direct_execution_endpoint_is_disabled(self):
        with app.app.test_request_context(json={}):
            response, status = app.execute_trade_api()
        self.assertEqual(status, 403)
        self.assertIn("接收球队", response.get_json()["error"])

    def test_only_receiver_can_accept_and_acceptance_executes_atomically(self):
        proposal = {
            "id": 7,
            "proposer_team_id": 10,
            "receiver_team_id": 20,
            "proposer_outgoing": "[101]",
            "receiver_outgoing": "[201]",
            "season": "2025-26",
            "status": "pending",
        }
        cursor = FakeCursor(proposal)
        valid_result = {"is_valid": True, "reasons": [], "executed": True}

        with app.app.test_request_context("/trade-proposals/7/accept", method="POST"):
            g.user = {"id": 2, "role": "management", "team_id": 20}
            with (
                patch.object(app, "transaction", fake_transaction_for(cursor)),
                patch.object(
                    app, "execute_trade_with_cursor", return_value=valid_result
                ) as execute_trade,
                patch.object(app, "invalidate_all"),
            ):
                response = app.accept_trade_proposal(7)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(execute_trade.call_args.args[0], cursor)
        payload = execute_trade.call_args.args[1]
        self.assertEqual(payload["team1_id"], 10)
        self.assertEqual(payload["team2_id"], 20)
        self.assertTrue(
            any("SET status = 'accepted'" in sql for sql, _ in cursor.executions)
        )

        unauthorized_cursor = FakeCursor(proposal)
        with app.app.test_request_context("/trade-proposals/7/accept", method="POST"):
            g.user = {"id": 3, "role": "management", "team_id": 30}
            with (
                patch.object(
                    app, "transaction", fake_transaction_for(unauthorized_cursor)
                ),
            ):
                with self.assertRaises(Forbidden):
                    app.accept_trade_proposal(7)

    def test_receiver_can_reject_and_sender_can_cancel_pending_proposal(self):
        proposal = {
            "proposer_team_id": 10,
            "receiver_team_id": 20,
            "status": "pending",
        }

        reject_cursor = FakeCursor(proposal)
        with app.app.test_request_context("/trade-proposals/7/reject", method="POST"):
            g.user = {"id": 2, "role": "management", "team_id": 20}
            with (
                patch.object(
                    app, "transaction", fake_transaction_for(reject_cursor)
                ),
            ):
                response = app.reject_trade_proposal(7)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            any("SET status = 'rejected'" in sql for sql, _ in reject_cursor.executions)
        )

        cancel_cursor = FakeCursor(proposal)
        with app.app.test_request_context("/trade-proposals/7/cancel", method="POST"):
            g.user = {"id": 1, "role": "management", "team_id": 10}
            with (
                patch.object(
                    app, "transaction", fake_transaction_for(cancel_cursor)
                ),
            ):
                response = app.cancel_trade_proposal(7)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            any("SET status = 'cancelled'" in sql for sql, _ in cancel_cursor.executions)
        )


if __name__ == "__main__":
    unittest.main()
