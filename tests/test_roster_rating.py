import unittest

from roster_rating import rate_roster_change


def player(position="PG", assists=3, points=18, known=True):
    return {"position":position,"sample":{
        "games":10,"seconds":21600,"points":points*10,"rebounds":50,
        "assists":assists*10,"threes":20,"turnovers":20,
    } if known else None}


class RosterRatingTests(unittest.TestCase):
    def setUp(self):
        self.outgoing=player()
        self.roster=[self.outgoing,player(),player("SG"),player("SG"),player("SF"),
                     player("SF"),player("PF"),player("PF"),player("C"),player("C")]
        self.needs=[{"metric":"assists","ours":18,"opponents":25}]

    def rate(self, incoming, **kwargs):
        return rate_roster_change(self.roster,incoming,[self.outgoing],self.needs,10,
                                  cap_amount=100_000_000,tax_line=130_000_000,
                                  projected_payroll=100_000_000,**kwargs)

    def test_basketball_help_outweighs_salary_savings(self):
        better=self.rate([player(assists=7)],salary_change=2_000_000)
        cheaper=self.rate([player(assists=2)],salary_change=-10_000_000)
        self.assertGreater(better["score"],cheaper["score"])
        self.assertIn("进攻发起点",better["summary"])
        self.assertNotEqual(cheaper["grade"],"S")
        self.assertEqual(sum(row["max"] for row in better["dimensions"]),100)

    def test_top_grade_requires_real_team_need_and_supported_upgrade(self):
        best=self.rate([player(assists=8)],salary_change=-5_000_000)
        self.assertEqual(best["grade"],"S")
        no_need=rate_roster_change(self.roster,[player(assists=8)],[self.outgoing],[],10,
                                  salary_change=-5_000_000,cap_amount=100_000_000)
        self.assertLessEqual(no_need["score"],79)
        missing=self.rate([player(known=False)],salary_change=-5_000_000)
        self.assertLessEqual(missing["score"],59)
        self.assertTrue(missing["limits"])

    def test_position_hole_and_invalid_trade_cannot_receive_good_grade(self):
        roster=[player("PG"),player("SG"),player("SF"),player("PF"),player("C")]
        hole=rate_roster_change(roster,[player("PF",assists=9)],[roster[0]],self.needs,10)
        self.assertIn(hole["grade"],{"D","E"})
        invalid=self.rate([player(assists=8)],valid=False)
        self.assertEqual((invalid["grade"],invalid["score"]),("E",0))

    def test_multi_player_comparison_weights_minutes_instead_of_summing_rates(self):
        one=self.rate([player(assists=6)])
        two=self.rate([player(assists=6),player(assists=6)])
        self.assertEqual(one["dimensions"][0]["score"],two["dimensions"][0]["score"])
        self.assertIn("不是胜率或加盟后统计预测",two["note"])


if __name__=="__main__":
    unittest.main()
