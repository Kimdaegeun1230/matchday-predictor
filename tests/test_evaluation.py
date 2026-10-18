import unittest
from collections import Counter

from football_predictor.agents.prediction_agent import predicted_outcome, score_matrix, summarize
from football_predictor.evaluation import round_accuracy


def p(mid, md, pred, actual, mode=None, media=None, probs=(0.5, 0.3, 0.2)):
    return {"match_id": mid, "matchday": md, "kickoff": f"2026-10-1{mid % 9}T14:00:00+00:00",
            "home_team": f"H{mid}", "away_team": f"A{mid}", "predicted_score": pred, "mode_score": mode or pred,
            "actual_score": actual, "home_win": probs[0], "draw": probs[1], "away_win": probs[2],
            "media": media or {}}


def oc(s):
    h, a = map(int, s.split("-"))
    return "H" if h > a else "D" if h == a else "A"


class ScoreVarietyTest(unittest.TestCase):
    def test_same_match_always_same_score(self):
        mat = score_matrix(1.7, 1.0, -0.1, 10)
        self.assertEqual(summarize(mat, seed=12345)["predicted_score"], summarize(mat, seed=12345)["predicted_score"])

    def test_score_agrees_with_outcome_and_varies(self):
        mat = score_matrix(2.2, 0.8, -0.1, 10)  # 홈 강세
        scores = Counter(summarize(mat, seed=s)["predicted_score"] for s in range(400))
        self.assertTrue(all(oc(s) == "H" for s in scores))
        self.assertLessEqual(len(scores), 5)            # 상위 5개 안에서만
        self.assertGreaterEqual(len(scores), 4)
        self.assertLess(scores.most_common(1)[0][1] / 400, 0.4)  # 한 스코어로 몰리지 않음
        self.assertIn("1-0", scores)
        self.assertTrue(any(int(s[0]) >= 3 for s in scores))   # 3골 이상도 나옴
        self.assertEqual(summarize(mat)["mode_score"], "2-0")

    def test_close_match_predicts_draw(self):
        self.assertEqual(predicted_outcome(0.36, 0.30, 0.34), "D")
        self.assertEqual(predicted_outcome(0.45, 0.27, 0.28), "H")
        draws = Counter(summarize(score_matrix(1.2, 1.15, -0.1, 10), seed=s)["predicted_score"] for s in range(200))
        self.assertTrue(all(oc(s) == "D" for s in draws))
        self.assertGreaterEqual(len(draws), 3)          # 0-0, 1-1, 2-2 …

    def test_pool_one_is_mode(self):
        from football_predictor.config import ModelConfig
        mat = score_matrix(2.2, 0.8, -0.1, 10)
        s = summarize(mat, ModelConfig(score_pool=1), seed=7)
        self.assertEqual(s["predicted_score"], s["mode_score"])


class RoundAccuracyTest(unittest.TestCase):
    def test_counts_outcome_and_exact_separately(self):
        reports = [
            {"date": "2026-10-10", "predictions": [
                p(1, 6, "2-1", "2-1"),            # 결과 ✓ 스코어 ✓
                p(2, 6, "3-1", "3-1", mode="1-0"),  # 결과 ✓ 대표 ✓, 최빈 ✗
                p(3, 6, "1-1", "0-2")]},          # 결과 ✗
            {"date": "2026-10-11", "predictions": [
                p(4, 6, "0-1", "0-1"),
                p(5, 7, "2-1", None)]},           # 7라운드는 아직 진행 중
        ]
        acc = round_accuracy(reports)
        r6, r7 = acc["rounds"]
        self.assertTrue(r6["complete"])
        self.assertEqual((r6["finished"], r6["outcome_hits"], r6["exact_hits"], r6["mode_exact_hits"]), (4, 3, 3, 2))
        self.assertEqual(r6["outcome_rate"], 0.75)
        self.assertFalse(r7["complete"])
        self.assertEqual(acc["latest_complete_round"], 6)
        self.assertEqual(acc["season"]["exact_rate"], 0.75)

    def test_media_effect_compares_model_only(self):
        media = {"count": 2, "model_only": {"H": 0.4, "D": 0.35, "A": 0.25}}
        reports = [{"date": "2026-10-10", "predictions": [
            p(1, 1, "0-1", "0-2", media=media, probs=(0.3, 0.3, 0.4))]}]  # 매체 반영으로 원정승으로 바뀌어 적중
        s = round_accuracy(reports)["season"]
        self.assertEqual((s["media_matches"], s["model_only_hits"], s["with_media_hits"]), (1, 0, 1))

    def test_same_match_counted_once(self):
        reports = [{"date": "2026-10-10", "predictions": [p(1, 1, "1-0", "1-0")]},
                   {"date": "2026-10-10", "predictions": [p(1, 1, "1-0", "1-0")]}]
        self.assertEqual(round_accuracy(reports)["rounds"][0]["matches"], 1)


if __name__ == "__main__":
    unittest.main()
