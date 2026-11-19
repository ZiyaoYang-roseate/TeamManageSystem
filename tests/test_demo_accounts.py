import unittest
from unittest.mock import patch

from init_demo_accounts import account_plan, initialize_demo_accounts


class DemoAccountTests(unittest.TestCase):
    def test_creates_three_roles_and_one_account_per_team(self):
        teams=[{"id":1,"name":"湖人","abbreviation":"LAL"},{"id":27,"name":"开拓者","abbreviation":"POR"}]
        plan=account_plan(teams,[])
        self.assertEqual([row["username"] for row in plan],["league_admin","scorekeeper","gm_lal","gm_por"])
        self.assertEqual({row["role"] for row in plan},{"league_admin","scorekeeper","management"})
        self.assertTrue(all(row["created"] for row in plan))

    def test_preserves_existing_team_account_and_password(self):
        plan=account_plan([{"id":1,"name":"湖人","abbreviation":"LAL"}],
                          [{"id":1,"username":"my_manager","role":"management","team_id":1}])
        self.assertEqual(plan[-1]["username"],"my_manager")
        self.assertFalse(plan[-1]["created"])

    def test_username_collision_cannot_take_over_another_role(self):
        with self.assertRaisesRegex(ValueError,"已用于其他身份"):
            account_plan([],[{"id":1,"username":"league_admin","role":"scorekeeper","team_id":None}])

    def test_demo_password_initialization_cannot_target_a_business_or_remote_database(self):
        for config in [{"host":"127.0.0.1","database":"nba_management_db"},
                       {"host":"remote.example","database":"nba_management_demo"}]:
            with patch.dict("init_demo_accounts.DB_CONFIG",config),patch("init_demo_accounts.transaction") as connection:
                with self.assertRaises(ValueError): initialize_demo_accounts()
                connection.assert_not_called()
