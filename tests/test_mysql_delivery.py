import os
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4

from app import app
from db import DB_CONFIG, execute, fetch_all, fetch_one, get_connection
from seed_data import validate_demo_games
from trade import validate_trade

SEASON = "2025-26"
TEST_DB = os.getenv("NBA_RUN_MYSQL_TESTS") == "1"


@unittest.skipUnless(TEST_DB, "requires a freshly initialized MySQL demo database")
class MySQLDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not DB_CONFIG["database"].endswith("_demo") or DB_CONFIG["host"] not in {"127.0.0.1", "localhost", "::1"}:
            raise RuntimeError("MySQL integration tests require a local _demo database")

    def client(self, name, role, team_id=None):
        execute(
            """INSERT INTO users (username,password_hash,role,team_id,display_name)
               VALUES (%s,'',%s,%s,%s)""",
            (name, role, team_id, name),
        )
        user = fetch_one("SELECT id FROM users WHERE username=%s", (name,))
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user["id"]
            session["_csrf_token"] = "integration"
        return client

    def test_fresh_delivery_from_pregame_to_review_and_archive(self):
        token = {"X-CSRF-Token": "integration"}
        with get_connection() as connection:
            self.assertEqual(validate_demo_games(connection), 135)
        fixture = fetch_one(
            """SELECT g.id,g.home_team_id,g.away_team_id
               FROM games g JOIN scorekeeper_business_dates d ON d.season=g.season AND d.business_date=g.game_date
               WHERE g.season=%s AND g.status='scheduled' ORDER BY g.id LIMIT 1""",
            (SEASON,),
        )
        home_id, away_id = fixture["home_team_id"], fixture["away_team_id"]
        game_id = fixture["id"]
        home, away = self.client("home", "management", home_id), self.client("away", "management", away_id)
        official = self.client("official", "league_admin")
        keeper = self.client("keeper", "scorekeeper")
        self.assertEqual(official.get("/league").status_code, 200)
        self.assertEqual(home.get("/my-team").status_code, 200)
        self.assertEqual(keeper.get("/scorekeeper/calendar").status_code, 200)
        urls = f"/api/scorekeeper/game/{game_id}"
        self.assertEqual(keeper.post(urls + "/start", headers=token).status_code, 409)
        players = {}
        for team, client in ((home_id, home), (away_id, away)):
            players[team] = [row["id"] for row in fetch_all(
                "SELECT id FROM players WHERE team_id=%s ORDER BY id", (team,)
            )]
            result = client.post(f"/games/{game_id}/starters", headers=token,
                                 data={"team_id": team, "player_ids": players[team][:5]})
            self.assertEqual(result.status_code, 303)
        self.assertEqual(keeper.post(urls + "/start", headers=token).status_code, 200)

        starter = players[home_id][0]
        free_agent = fetch_one("SELECT id FROM players WHERE team_id IS NULL ORDER BY id LIMIT 1")["id"]
        self.assertEqual(home.post(f"/api/players/{starter}/waive", headers=token).status_code, 409)
        self.assertEqual(home.post(f"/api/players/{free_agent}/sign", headers=token,
                                   json={"salary": 1200000, "season": SEASON}).status_code, 409)
        self.assertEqual(official.post(f"/players/delete/{starter}", headers=token).status_code, 409)
        self.assertEqual(official.post(f"/teams/delete/{home_id}", headers=token).status_code, 409)

        trade = None
        for sent in players[home_id]:
            for received in players[away_id]:
                option = {"team1_id": home_id, "team2_id": away_id, "team1_outgoing_player_ids": [sent],
                          "team2_outgoing_player_ids": [received], "season": SEASON}
                if validate_trade(option)["is_valid"]:
                    trade = option
                    break
            if trade:
                break
        self.assertIsNotNone(trade, "demo needs at least one affordable trade between the teams")
        proposal = home.post("/api/trade-proposals", headers=token, json=trade)
        self.assertEqual(proposal.status_code, 201)
        proposal_id = proposal.get_json()["proposal_id"]
        self.assertEqual(away.post(f"/trade-proposals/{proposal_id}/accept", headers=token).status_code, 302)
        self.assertEqual(fetch_one("SELECT status FROM trade_proposals WHERE id=%s", (proposal_id,))["status"], "pending")

        shot = {"player_id": starter, "action": "add_point", "value": 2, "shot_x": 0, "shot_y": 390,
                "request_id": str(uuid4())}
        self.assertEqual(keeper.post(urls + "/update", headers=token, json=shot).status_code, 200)
        self.assertEqual(keeper.post(urls + "/update", headers=token, json=shot).status_code, 200)
        self.assertEqual(fetch_one("SELECT home_score FROM games WHERE id=%s", (game_id,))["home_score"], 2)
        for period in range(1, 5):
            if period > 1:
                self.assertEqual(keeper.post(urls + "/clock", headers=token,
                                             json={"period": period, "seconds_remaining": 720}).status_code, 200)
            self.assertEqual(keeper.post(urls + "/clock", headers=token,
                                         json={"period": period, "seconds_remaining": 0}).status_code, 200)
        self.assertEqual(keeper.post(urls + "/end", headers=token).status_code, 200)
        with get_connection() as connection:
            self.assertEqual(validate_demo_games(connection), 136)
        audit = f"/league/games/{game_id}"
        self.assertEqual(official.post(audit, headers=token, data={"action": "open", "note": "Check score"}).status_code, 303)
        self.assertEqual(official.post(audit, headers=token, data={"action": "resolve", "note": "Matched"}).status_code, 303)
        route = f"/games/{game_id}/analysis/review"
        with patch("analysis_routes.configured_model", return_value=None):
            result = home.post(route, headers=token)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(away.get(result.location).status_code, 200)
        heatmap = "/shot-chart?" + urlencode({"season": SEASON, "player_id": starter, "zone": "Restricted Area"})
        self.assertIn("定位回合", home.get(heatmap).get_data(as_text=True))

        self.assertEqual(away.post(f"/trade-proposals/{proposal_id}/accept", headers=token).status_code, 302)
        self.assertEqual(fetch_one("SELECT status FROM trade_proposals WHERE id=%s", (proposal_id,))["status"], "accepted")
        current_team = fetch_one("SELECT team_id FROM players WHERE id=%s", (starter,))["team_id"]
        self.assertEqual(official.post(f"/players/{starter}/archive", headers=token).status_code, 302)
        profile = away.get(f"/players/{starter}")
        self.assertEqual(profile.status_code, 200)
        self.assertIn("已归档", profile.get_data(as_text=True))
        self.assertFalse(any(row["id"] == starter for row in away.get(
            f"/api/teams/{current_team}/players").get_json()))
        self.assertIsNotNone(fetch_one("SELECT id FROM player_game_stats WHERE game_id=%s AND player_id=%s",
                                       (game_id, starter)))

        self.assertEqual(official.post(f"/teams/{home_id}/archive", headers=token).status_code, 302)
        self.assertEqual(fetch_one("SELECT status FROM games WHERE id=%s", (game_id,))["status"], "finished")
        self.assertGreater(fetch_one("SELECT COUNT(*) AS total FROM games WHERE status='cancelled' AND (home_team_id=%s OR away_team_id=%s)",
                                    (home_id, home_id))["total"], 0)
        self.assertEqual(away.get(f"/games/{game_id}").status_code, 200)
        self.assertEqual(away.get(result.location).status_code, 200)
        self.assertEqual(home.get("/my-team").status_code, 302)
