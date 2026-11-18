import unittest
from unittest.mock import patch

import app


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        self.token = "test-csrf-token"
        with self.client.session_transaction() as session:
            session["user_id"] = 1
            session["_csrf_token"] = self.token
        self.headers = {"X-CSRF-Token": self.token}
        self.manager = {"id": 1, "role": "management", "team_id": 10, "display_name": "GM"}
        user_lookup = patch.object(app, "get_user_by_id", return_value=self.manager)
        user_lookup.start()
        self.addCleanup(user_lookup.stop)

    def test_league_writes_require_admin_even_with_valid_csrf(self):
        with patch.object(app, "execute") as execute:
            response = self.client.post("/teams/delete/20", headers=self.headers)
            self.assertEqual(response.status_code, 403)
            execute.assert_not_called()

    def test_league_operations_is_closed_to_team_managers(self):
        with patch.object(app, "league_overview") as overview:
            response = self.client.get("/league")
            self.assertEqual(response.status_code, 403)
            overview.assert_not_called()

    def test_private_workspaces_reject_non_managers_before_querying(self):
        for role in ("league_admin", "scorekeeper"):
            self.manager.update(role=role, team_id=None)
            with self.subTest(role=role), patch("team_planning.transaction") as tx:
                self.assertEqual(self.client.get("/salary").status_code, 403)
                self.assertEqual(self.client.get("/scouting").status_code, 403)
                self.assertEqual(self.client.post("/scouting", headers=self.headers).status_code, 403)
                self.assertEqual(self.client.post("/api/salary/preview/trade", json={}, headers=self.headers).status_code, 403)
                tx.assert_not_called()

    def test_both_business_date_urls_require_league_admin(self):
        for role in ("management", "scorekeeper"):
            self.manager["role"] = role
            for url in ("/league/business-date", "/scorekeeper/calendar/date"):
                with self.subTest(role=role, url=url), patch.object(app, "transaction") as tx:
                    self.assertEqual(self.client.post(url, headers=self.headers).status_code, 403)
                    tx.assert_not_called()

    def test_league_admin_cannot_access_team_pregame_analysis_or_submit_starters(self):
        self.manager.update(role="league_admin", team_id=None)
        with patch("analysis_routes.transaction") as tx:
            self.assertEqual(self.client.get("/games/20/analysis/scout").status_code, 403)
            self.assertEqual(self.client.post("/games/20/starters", headers=self.headers).status_code, 403)
            self.assertEqual(self.client.post("/games/20/availability", headers=self.headers).status_code, 403)
            tx.assert_not_called()

    def test_team_info_write_requires_ownership(self):
        with patch.object(app, "get_team_or_404") as get_team:
            response = self.client.post("/teams/edit/20", headers=self.headers)
            self.assertEqual(response.status_code, 403)
            get_team.assert_not_called()

    def test_write_without_csrf_is_rejected(self):
        with patch("free_agency_api.transaction") as transaction:
            response = self.client.delete("/api/free-agency/targets/1")
            self.assertEqual(response.status_code, 403)
            transaction.assert_not_called()

    def test_invalid_json_and_years_fail_before_database_writes(self):
        with patch("free_agency_api.transaction") as transaction:
            for body in ([1], {"target_id": 1, "years": "invalid"}):
                with self.subTest(body=body):
                    response = self.client.post("/api/free-agency/offers", json=body, headers=self.headers)
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("error", response.get_json())
            transaction.assert_not_called()

    def test_logout_requires_post_and_csrf(self):
        self.assertEqual(self.client.get("/logout").status_code, 405)
        self.assertEqual(self.client.post("/logout").status_code, 403)
        self.assertEqual(self.client.post("/logout", headers=self.headers).status_code, 302)
        with self.client.session_transaction() as session:
            self.assertNotIn("user_id", session)

    def test_templates_compile(self):
        for name in app.app.jinja_env.list_templates():
            with self.subTest(template=name):
                app.app.jinja_env.get_template(name)

    def test_trade_page_uses_requested_season_for_roster_and_proposal(self):
        team = {"id": 10, "name": "Home", "city": "City"}
        with (
            patch.object(app, "get_team_or_404", return_value=team),
            patch.object(app, "get_teams", return_value=[team]),
        ):
            response = self.client.get("/trade-machine?season=2024-25")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'const tradeSeason = "2024-25";', response.data)
        self.assertIn(b"season: tradeSeason", response.data)
