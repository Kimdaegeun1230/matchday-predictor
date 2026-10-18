import unittest

from football_predictor.agents.prediction_agent import xg_score
from football_predictor.evaluation import round_accuracy


def p(mid, md, pred, actual, xg=None, media=None, probs=(0.5, 0.3, 0.2)):
    return {"match_id": mid, "matchday": md, "kickoff": f"2026-10-1{mid % 9}T14:00:00+00:00",
            "home_team": f"H{mid}", "away_team": f"A{mid}", "predicted_score": pred, "xg_score": xg or pred,
            "actual_score": actual, "home_win": probs[0], "draw": probs[1], "away_win": probs[2],
            "media": media or {}}


class XgScoreTest(unittest.TestCase):
    def test_rounds_and_respects_outcome(self):
        self.assertEqual(xg_score(2.73, 1.02, "H"), "3-1")
        self.assertEqual(xg_score(1.4, 1.3, "H"), "2-1")   # 반올림하면 1-1 이지만 홈승 예측과 맞춤
        self.assertEqual(xg_score(0.8, 1.2, "A"), "1-2")
        self.assertEqual(xg_score(1.6, 1.1, "D"), "1-1")


class RoundAccuracyTest(unittest.TestCase):
    def test_counts_outcome_and_exact_separately(self):
        reports = [
            {"date": "2026-10-10", "predictions": [
                p(1, 6, "2-1", "2-1"),            # 결과 ✓ 스코어 ✓
                p(2, 6, "1-0", "3-1", xg="3-1"),  # 결과 ✓ 최빈 ✗, xG ✓
                p(3, 6, "1-1", "0-2")]},          # 결과 ✗
            {"date": "2026-10-11", "predictions": [
                p(4, 6, "0-1", "0-1"),
                p(5, 7, "2-1", None)]},           # 7라운드는 아직 진행 중
        ]
        acc = round_accuracy(reports)
        r6, r7 = acc["rounds"]
        self.assertTrue(r6["complete"])
        self.assertEqual((r6["finished"], r6["outcome_hits"], r6["exact_hits"], r6["xg_exact_hits"]), (4, 3, 2, 3))
        self.assertEqual(r6["outcome_rate"], 0.75)
        self.assertFalse(r7["complete"])
        self.assertEqual(acc["latest_complete_round"], 6)
        self.assertEqual(acc["season"]["exact_rate"], 0.5)

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
