import unittest
from collections import Counter

from football_predictor.agents.prediction_agent import predicted_outcome, score_matrix, summarize
from football_predictor.evaluation import round_accuracy


def p(mid, md, pred, actual, mode=None, media=None, probs=(0.5, 0.3, 0.2)):
    return {"match_id": mid, "matchday": md, "kickoff": f"2026-10-1{mid % 9}T14:00:00+00:00",
            "home_team": f"H{mid}", "away_team": f"A{mid}", "predicted_score": pred, "mode_score": mode or pred,
            "actual_score": actual, "home_win": probs[0], "draw": probs[1], "away_win": probs[2],
            "media": media or {}}


class ScoreVarietyTest(unittest.TestCase):
    def test_strong_favourite_gets_big_score(self):
        s = summarize(score_matrix(2.8, 0.7, -0.1, 10))
        h, a = map(int, s["predicted_score"].split("-"))
        self.assertGreaterEqual(h, 3)            # 최빈 스코어는 2-0 이지만 대표 스코어는 3골 이상
        self.assertGreater(h, a)
        self.assertEqual(s["mode_score"], "2-0")

    def test_close_match_predicts_draw(self):
        self.assertEqual(predicted_outcome(0.36, 0.30, 0.34), "D")
        self.assertEqual(predicted_outcome(0.45, 0.27, 0.28), "H")
        low = summarize(score_matrix(0.9, 0.85, -0.1, 10))
        high = summarize(score_matrix(1.5, 1.45, -0.1, 10))  # 팽팽하면서 2.5골 초과 확률 50% 이상
        self.assertIn(low["predicted_score"], ("0-0", "1-1"))
        self.assertEqual(high["predicted_score"], "2-2")

    def test_range_of_scores_across_strengths(self):
        scores = Counter(summarize(score_matrix(l, m, -0.1, 10))["predicted_score"]
                         for l in (0.6, 0.9, 1.2, 1.5, 1.8, 2.1, 2.5, 3.0)
                         for m in (0.5, 0.8, 1.1, 1.4, 1.8, 2.2))
        self.assertGreaterEqual(len(scores), 8)
        self.assertTrue(any(max(map(int, s.split("-"))) >= 3 for s in scores))
        self.assertTrue(any(s[0] == s[2] for s in scores))  # 무승부 스코어도 나옴


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
