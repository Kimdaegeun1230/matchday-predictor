"""NBA 파이프라인: 가짜 경기·부상 데이터로 모델·부상·매체·적중률을 네트워크 없이 검증."""
import json
import math
import random
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from football_predictor.config import ALL_LEAGUES, NBA
from football_predictor.nba.engine import NbaEngine, margin_buckets, predicted_score
from football_predictor.nba.evaluation import nba_accuracy
from football_predictor.nba.sources import NbaGame, NbaInjury, NbaTeam, season_of

TODAY = date.today()
SEASON = season_of(TODAY)
STRENGTH = {"celtics": 8, "thunder": 7, "knicks": 3, "lakers": 0, "hornets": -6, "wizards": -9}
NAMES = {"celtics": "Boston Celtics", "thunder": "Oklahoma City Thunder", "knicks": "New York Knicks",
         "lakers": "Los Angeles Lakers", "hornets": "Charlotte Hornets", "wizards": "Washington Wizards"}


class FakeGames:
    key, name, url, license, attribution, enabled = "balldontlie", "balldontlie", "", "", "", True

    def __init__(self):
        rng = random.Random(7)
        teams = list(STRENGTH)
        self.games, gid = [], 1
        start = TODAY - timedelta(days=60)
        for d in range(60):
            day = start + timedelta(days=d)
            rng.shuffle(teams)
            for h, a in ((teams[0], teams[1]), (teams[2], teams[3]), (teams[4], teams[5])):
                hp = round(114 + 1.5 + STRENGTH[h] / 2 - STRENGTH[a] / 2 + rng.gauss(0, 9))
                ap = round(114 - 1.5 + STRENGTH[a] / 2 - STRENGTH[h] / 2 + rng.gauss(0, 9))
                if hp == ap:
                    hp += 1
                self.games.append(NbaGame(gid, day, datetime.combine(day, datetime.min.time(), timezone.utc),
                                          h, a, "FINISHED", hp, ap, season=SEASON))
                gid += 1
        # 오늘 경기: 강팀 홈 vs 약팀, 비슷한 두 팀
        self.games.append(NbaGame(9001, TODAY, None, "celtics", "wizards", "SCHEDULED", season=SEASON))
        self.games.append(NbaGame(9002, TODAY, None, "lakers", "knicks", "SCHEDULED", season=SEASON))

    def teams(self):
        return {k: NbaTeam(k, NAMES[k], k[:3].upper(), "East" if k in ("celtics", "knicks", "hornets", "wizards") else "West")
                for k in STRENGTH}

    def season_games(self, season, current):
        return [g for g in self.games if g.season == season]


class FakeInjuries:
    key, name, url, license, attribution, enabled = "espn", "ESPN", "", "비공식", "", True

    def __init__(self, injuries=()):
        self._inj = list(injuries)

    def injuries(self):
        return self._inj


def engine(injuries=(), media_path=None):
    return NbaEngine(ALL_LEAGUES["NBA"], FakeGames(), FakeInjuries(injuries),
                     media_path=media_path or Path(tempfile.gettempdir()) / "no-nba-media.json")


class NbaModelTest(unittest.TestCase):
    def test_strong_home_team_favoured(self):
        r = engine().predict(TODAY)
        p = {x["match_id"]: x for x in r["predictions"]}
        cel = p[9001]
        self.assertGreater(cel["home_win"], 0.75)
        h, a = map(int, cel["predicted_score"].split("-"))
        self.assertGreater(h, a)
        self.assertTrue(95 <= a <= 135 and 95 <= h <= 140)
        self.assertAlmostEqual(cel["home_win"] + cel["away_win"], 1.0, places=3)
        self.assertEqual(cel["draw"], 0.0)
        self.assertAlmostEqual(sum(b["prob"] for b in cel["margin_buckets"]), 1.0, places=3)
        top = sorted(r["teams"], key=lambda t: t["rating"], reverse=True)[0]
        self.assertEqual(top["team"], "celtics")
        self.assertEqual([e["agent"] for e in r["agent_log"]],
                         ["nba_data", "nba_quality", "nba_team", "nba_injury", "nba_predictor", "nba_media"])

    def test_star_injury_lowers_team_score(self):
        base = {x["match_id"]: x for x in engine().predict(TODAY)["predictions"]}[9001]
        star = NbaInjury("celtics", "Star Player", "Out", 0.0, "knee", ppg=28.0, mpg=35, games_played=55)
        bench = NbaInjury("celtics", "Bench Player", "Out", 0.0, "ankle", ppg=3.0, mpg=8, games_played=3)
        hurt = {x["match_id"]: x for x in engine([star, bench]).predict(TODAY)["predictions"]}[9001]
        raw = 28.0 * NBA.injury_replacement                               # 벤치(출전 적음)는 제외
        self.assertAlmostEqual(base["home_xg"] - hurt["home_xg"], NBA.injury_cap * (1 - math.exp(-raw / NBA.injury_cap)), delta=0.2)
        self.assertLess(hurt["home_win"], base["home_win"])
        missing = hurt["home_squad"]["missing"]
        self.assertEqual(missing[0]["name"], "Star Player")
        self.assertFalse(missing[1]["counted"])

    def test_no_ties_and_buckets(self):
        self.assertEqual(predicted_score(110.4, 110.2), "111-110")
        self.assertEqual(predicted_score(108.6, 112.4), "109-112")
        b = margin_buckets(0, 12)
        self.assertAlmostEqual(b[3]["prob"], b[4]["prob"], places=4)

    def test_media_blend_moves_margin(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nba_media.json"
            path.write_text(json.dumps({"predictions": [
                {"fixture_id": "9002", "home": "", "away": "", "kickoff": "", "outlet": "Yahoo Sports",
                 "author": "Writer", "home_goals": 100, "away_goals": 120, "url": "https://sports.yahoo.com/x",
                 "headline": "", "collected_at": ""}], "collected": {}}), encoding="utf-8")
            base = {x["match_id"]: x for x in engine().predict(TODAY)["predictions"]}[9002]
            r = engine(media_path=path).predict(TODAY)
            p = {x["match_id"]: x for x in r["predictions"]}[9002]
            self.assertLess(p["margin"], base["margin"])          # 매체는 원정 20점 차 승리 예측
            self.assertEqual(p["media"]["count"], 1)
            self.assertTrue(r["media"]["available"])

    def test_calendar_and_accuracy(self):
        eng = engine()
        cal = eng.calendar(TODAY)
        self.assertEqual(cal[-1]["date"], TODAY.isoformat())
        reports = [eng.predict(date.fromisoformat(d["date"])) for d in cal[-15:-1]]
        acc = nba_accuracy(reports)
        s = acc["season"]
        self.assertEqual(s["finished"], 14 * 3)
        self.assertGreater(s["outcome_rate"], 0.5)                # 강약 차가 분명한 가짜 리그
        self.assertIsNotNone(s["mean_abs_err"])
        self.assertLess(s["mean_abs_err"], 15)


if __name__ == "__main__":
    unittest.main()
