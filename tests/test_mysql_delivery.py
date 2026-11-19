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

    def test_midgame_completion_from_league_page_and_lineup_recommendation(self):
        fixture = fetch_one(
            """SELECT g.* FROM games g JOIN teams h ON h.id=g.home_team_id JOIN teams a ON a.id=g.away_team_id
               WHERE g.status='scheduled' AND g.season=%s AND h.archived_at IS NULL AND a.archived_at IS NULL
               ORDER BY g.game_date,g.id LIMIT 1""", (SEASON,)
        )
        gid = fixture["id"]
        execute("UPDATE scorekeeper_business_dates SET business_date=%s WHERE season=%s",
                (fixture["game_date"], SEASON))
        clients = {}
        for role, team in (("league_admin", None), ("scorekeeper", None), ("management", fixture["home_team_id"])):
            user = fetch_one("SELECT id FROM users WHERE team_id=%s", (team,)) if team else fetch_one(
                "SELECT id FROM users WHERE role=%s LIMIT 1", (role,)
            )
            if user is None:
                clients[role] = self.client("midgame_manager", role, team)
            else:
                clients[role] = app.test_client()
                with clients[role].session_transaction() as session:
                    session["user_id"] = user["id"]
                    session["_csrf_token"] = "integration"
        token = {"X-CSRF-Token": "integration"}
        manager, official, keeper = (clients[key] for key in ("management", "league_admin", "scorekeeper"))
        with patch("analysis_routes.configured_model", return_value=None):
            report = manager.post(f"/games/{gid}/analysis/scout", headers=token)
        self.assertEqual(report.status_code, 303)
        self.assertIn("推荐本队首发五人", manager.get(report.location).get_data(as_text=True))
        for team in (fixture["home_team_id"], fixture["away_team_id"]):
            players = fetch_all("SELECT id FROM players WHERE team_id=%s AND archived_at IS NULL ORDER BY id LIMIT 5", (team,))
            for player in players:
                execute("INSERT INTO game_starters(game_id,team_id,player_id) VALUES (%s,%s,%s)", (gid, team, player["id"]))
        base = f"/api/scorekeeper/game/{gid}"
        self.assertEqual(keeper.post(base + "/start", headers=token).status_code, 200)
        self.assertEqual(official.post(f"/league/games/{gid}", data={"action": "finish"}, headers=token).status_code, 303)
        self.assertEqual(fetch_one("SELECT status FROM games WHERE id=%s", (gid,))["status"], "live")
        actor = fetch_one("SELECT player_id FROM game_starters WHERE game_id=%s LIMIT 1", (gid,))["player_id"]
        self.assertEqual(keeper.post(base + "/update", json={"player_id": actor, "action": "add_point", "value": 1}, headers=token).status_code, 200)
        self.assertEqual(manager.post(f"/league/games/{gid}", data={"action": "finish"}, headers=token).status_code, 403)
        self.assertEqual(official.post(f"/league/games/{gid}", data={"action": "finish"}, headers=token).status_code, 303)
        final = fetch_one("SELECT status,period,seconds_remaining FROM games WHERE id=%s", (gid,))
        self.assertEqual(final, {"status": "finished", "period": 1, "seconds_remaining": 720})
        self.assertIn("返回比赛处理", official.get(f"/games/{gid}?origin=league").get_data(as_text=True))
        self.assertEqual(official.get("/league").status_code, 200)

    def test_new_player_directory_draft_assistant_and_multi_trade_application(self):
        from roster_recommendations import trade_candidates
        from player_directory import team_members
        from schedule_order import ordered_games
        from flask import template_rendered

        team = fetch_one("SELECT id FROM teams WHERE archived_at IS NULL ORDER BY id DESC LIMIT 1")["id"]
        manager = self.client("new_features_manager", "management", team)
        keeper = self.client("new_features_keeper", "scorekeeper")
        # The candidate search considers only teams that have a receiving manager.
        for row in fetch_all("SELECT t.id FROM teams t WHERE t.archived_at IS NULL AND NOT EXISTS (SELECT 1 FROM users u WHERE u.team_id=t.id AND u.role='management')"):
            self.client(f"new_features_gm_{row['id']}", "management", row["id"])
        for mode in ("average", "total"):
            for metric in ("points", "rebounds", "assists", "steals", "blocks", "threes"):
                response = manager.get(f"/analytics?season={SEASON}&mode={mode}&metric={metric}")
                self.assertEqual(response.status_code, 200)
                self.assertIn("完整榜单", response.get_data(as_text=True))
        self.assertEqual(manager.get("/players").status_code, 302)
        own = manager.get(f"/my-team?season={SEASON}").get_data(as_text=True)
        for member in team_members(team, SEASON):
            self.assertIn(member["name"], own)
        keeper_page = keeper.get("/analytics").get_data(as_text=True)
        self.assertNotIn('href="/compare', keeper_page)
        profile_id = fetch_one("SELECT id FROM players WHERE team_id=%s LIMIT 1", (team,))["id"]
        self.assertNotIn('href="/compare', keeper.get(f"/players/{profile_id}").get_data(as_text=True))
        contexts = []
        def record(sender, template, context, **extra):
            contexts.append(context)
        template_rendered.connect(record, app)
        try:
            self.assertEqual(manager.get(f"/games?season={SEASON}&per_page=100").status_code, 200)
            context = contexts[-1]
            self.assertEqual(context["games"], ordered_games(context["games"], context["business_date"]))
            self.assertEqual(keeper.get(f"/scorekeeper/calendar?season={SEASON}").status_code, 200)
            context = contexts[-1]
            self.assertEqual(context["games"], ordered_games(context["games"], context["business_date"]))
        finally:
            template_rendered.disconnect(record, app)
        with patch("analysis_model.configured_model", return_value=None):
            draft = manager.post(f"/scouting?season={SEASON}", headers={"X-CSRF-Token":"integration"},
                                 data={"action":"recommend", "year":2026})
        self.assertEqual(draft.status_code, 200)
        self.assertIn("试训重点", draft.get_data(as_text=True))
        candidates, _, _, _ = trade_candidates(team, SEASON)
        multi = next((row for row in candidates if len(row["payload"]["team1_outgoing_player_ids"])+len(row["payload"]["team2_outgoing_player_ids"])>2), None)
        self.assertIsNotNone(multi, "demo must supply at least one legal multi-player option")
        payload = multi["payload"]
        self.assertIn(multi["rating"]["grade"], {"S","A","B","C","D","E"})
        self.assertEqual(len(multi["rating"]["dimensions"]),3)
        self.assertTrue(validate_trade(payload)["is_valid"])
        with patch("decision_analysis.configured_model",return_value=None):
            review=manager.post("/api/trades/analysis",headers={"X-CSRF-Token":"integration"},json=payload)
        self.assertEqual(review.status_code,200)
        self.assertIn(review.get_json()["rating"]["grade"],{"S","A","B","C","D","E"})
        response = manager.post("/api/trade-proposals", headers={"X-CSRF-Token":"integration"}, json=payload)
        self.assertEqual(response.status_code, 201)
        rival = fetch_one("SELECT id FROM users WHERE team_id=%s", (payload["team2_id"],))
        receiver = app.test_client()
        with receiver.session_transaction() as session:
            session["user_id"] = rival["id"]
            session["_csrf_token"] = "integration"
        proposal = response.get_json()["proposal_id"]
        receiver.post(f"/trade-proposals/{proposal}/accept", headers={"X-CSRF-Token":"integration"})
        self.assertEqual(fetch_one("SELECT status FROM trade_proposals WHERE id=%s", (proposal,))["status"], "accepted")
        for player_id in payload["team2_outgoing_player_ids"]:
            self.assertEqual(fetch_one("SELECT team_id FROM players WHERE id=%s", (player_id,))["team_id"], team)
            from player_profiles import player_career_teams
            career=player_career_teams(player_id)
            self.assertTrue({team,payload["team2_id"]}<={row["id"] for row in career})
            self.assertNotIn(player_id,{row["id"] for row in team_members(payload["team2_id"],SEASON)})
            page=manager.get(f"/players/{player_id}").get_data(as_text=True)
            self.assertIn("生涯效力球队",page)

    def test_zz_signing_and_combined_plans_expose_value_grades(self):
        user=fetch_one("SELECT u.id,u.team_id FROM users u JOIN teams t ON t.id=u.team_id WHERE u.role='management' AND t.archived_at IS NULL ORDER BY t.id DESC LIMIT 1")
        manager=app.test_client()
        with manager.session_transaction() as session:
            session["user_id"]=user["id"]
            session["_csrf_token"]="integration"
        free_agent=fetch_one("SELECT id FROM players WHERE team_id IS NULL AND archived_at IS NULL ORDER BY id LIMIT 1")["id"]
        target=fetch_one("SELECT id FROM free_agency_targets WHERE team_id=%s AND player_id=%s",(user["team_id"],free_agent))
        if not target:
            execute("INSERT INTO free_agency_targets(team_id,player_id,tier,priority_order) VALUES (%s,%s,'Vet Min',1)",(user["team_id"],free_agent))
            target=fetch_one("SELECT id FROM free_agency_targets WHERE team_id=%s AND player_id=%s",(user["team_id"],free_agent))
        token={"X-CSRF-Token":"integration"}
        with patch("decision_analysis.configured_model",return_value=None):
            result=manager.post("/api/free-agency/analysis",headers=token,json={"target_id":target["id"],"season":SEASON,"years":1,"year1_salary":1200000})
        self.assertEqual(result.status_code,200)
        self.assertIn("rating",result.get_json())
        self.assertEqual(result.get_json()["rating"]["method"],"赛场补强60% · 轮换适配25% · 薪资成本15%")
        with patch("roster_recommendations.configured_model",return_value=None):
            plans=manager.post("/api/free-agency/recommendations",headers=token,json={"season":SEASON})
            packages=manager.post(f"/salary?season={SEASON}",headers=token)
        self.assertEqual(plans.status_code,200)
        self.assertTrue(plans.get_json()["plans"])
        self.assertTrue(all(plan.get("rating") for plan in plans.get_json()["plans"]))
        self.assertEqual(packages.status_code,200)
        self.assertIn("本队补强评级",packages.get_data(as_text=True))
