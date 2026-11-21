import unittest
from html.parser import HTMLParser
from urllib.parse import urlsplit

from flask import g, render_template

import app


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = set()

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            value = dict(attrs).get("href", "")
            if value.startswith("/"):
                self.paths.add(urlsplit(value).path)


class RoleNavigationTests(unittest.TestCase):
    def navigation(self, role):
        with app.app.test_request_context("/"):
            g.user = {"id": 1, "role": role, "team_id": 10 if role == "management" else None,
                      "display_name": "测试用户"}
            html = render_template("base.html", season="2025-26")
        links = Links()
        links.feed(html)
        self.assertNotIn("阵容组合", html)
        self.assertNotIn("球员档案", html)
        self.assertTrue({"/lineups", "/players"}.isdisjoint(links.paths))
        return links.paths

    def test_manager_navigation_has_own_team_and_private_workspaces(self):
        paths = self.navigation("management")
        self.assertTrue({"/my-team", "/salary", "/scouting", "/free-agency", "/trade-machine"} <= paths)
        self.assertTrue({"/league", "/league/draft-pool", "/scorekeeper/calendar", "/teams"}.isdisjoint(paths))

    def test_admin_navigation_has_public_pool_but_no_private_team_planning(self):
        paths = self.navigation("league_admin")
        self.assertTrue({"/league", "/teams", "/league/draft-pool"} <= paths)
        self.assertTrue({"/salary", "/scouting", "/free-agency", "/trade-machine", "/scorekeeper/calendar"}.isdisjoint(paths))

    def test_scorekeeper_navigation_has_no_management_or_league_entry(self):
        paths = self.navigation("scorekeeper")
        self.assertIn("/scorekeeper/calendar", paths)
        self.assertTrue({"/league", "/teams", "/my-team", "/games", "/salary", "/scouting", "/compare"}.isdisjoint(paths))


if __name__ == "__main__":
    unittest.main()
