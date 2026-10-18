import unittest
from datetime import date

from football_predictor.agents.base import MatchContext
from football_predictor.agents.coordinator import CoordinatorAgent
from football_predictor.agents.prediction_agent import score_matrix
from football_predictor.config import LEAGUES, get_league


class ScoreMatrixTest(unittest.TestCase):
    def test_probabilities_sum_to_one(self):
        mat = score_matrix(1.6, 1.1, -0.1, 10)
        self.assertAlmostEqual(sum(map(sum, mat)), 1.0, places=9)

    def test_stronger_lambda_favours_home(self):
        mat = score_matrix(2.5, 0.6, -0.1, 10)
        home = sum(mat[i][j] for i in range(11) for j in range(11) if i > j)
        away = sum(mat[i][j] for i in range(11) for j in range(11) if i < j)
        self.assertGreater(home, away)


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.coord = CoordinatorAgent(source="sample", write_files=False)

    def test_all_leagues_produce_predictions(self):
        target = date(2026, 10, 18)
        for key in LEAGUES:
            report = self.coord.predict(key, target)
            self.assertGreater(len(report["predictions"]), 0, key)
            for p in report["predictions"]:
                self.assertAlmostEqual(p["home_win"] + p["draw"] + p["away_win"], 1.0, places=3)
                h, a = map(int, p["predicted_score"].split("-"))
                self.assertGreaterEqual(min(h, a), 0)
            agents = [entry["agent"] for entry in report["agent_log"]]
            self.assertEqual(agents, ["data_collector", "data_quality", "team_analyst", "player_analyst",
                                      "predictor", "reporter"])

    def test_strong_team_rated_above_weak_team(self):
        ctx = MatchContext(league=get_league("EPL"), target_date=date(2027, 2, 1))
        for agent in self.coord.build_team():
            if agent.name in ("data_collector", "team_analyst"):
                agent(ctx)
        by_name = {t.name: tid for tid, t in ctx.teams.items()}
        strong = ctx.team_strengths[by_name["Liverpool FC"]]
        weak = ctx.team_strengths[by_name["Burnley FC"]]
        self.assertGreater(strong.attack_home, weak.attack_home)
        self.assertLess(strong.defense_away, weak.defense_away)

    def test_unknown_league_rejected(self):
        with self.assertRaises(ValueError):
            self.coord.predict("SERIEA", date(2026, 10, 18))


if __name__ == "__main__":
    unittest.main()
