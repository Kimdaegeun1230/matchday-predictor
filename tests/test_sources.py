"""출처 어댑터와 병합 규칙을 네트워크 없이 검증 (가짜 응답 사용)."""
import unittest
from datetime import date
from unittest import mock

from football_predictor.agents.base import MatchContext
from football_predictor.agents.data_agent import DataCollectorAgent
from football_predictor.agents.player_agent import PlayerAnalystAgent
from football_predictor.agents.quality_agent import DataQualityAgent
from football_predictor.config import get_league
from football_predictor.providers import multi, sources
from football_predictor.providers.multi import MultiSourceProvider, player_key
from football_predictor.providers.teamnames import team_key

EPL = get_league("EPL")
TODAY = date(2026, 10, 18)

OPENFOOTBALL_NOW = {"matches": [
    # BST 기간: 영국 현지 15:00 = UTC 14:00
    {"round": "Matchday 1", "date": "2026-08-22", "time": "15:00", "team1": "Arsenal FC",
     "team2": "Coventry City FC", "score": {"ft": [3, 0]}},
    {"round": "Matchday 2", "date": "2026-08-29", "time": "15:00", "team1": "Manchester City FC",
     "team2": "Arsenal FC", "score": [1, 1]},
    {"round": "Matchday 3", "date": "2026-09-05", "time": "15:00", "team1": "Coventry City FC",
     "team2": "Manchester City FC"},  # openfootball 은 아직 결과 없음
    {"round": "Matchday 7", "date": "2026-10-18", "time": "14:00", "team1": "Arsenal FC",
     "team2": "Manchester City FC"},
]}
OPENFOOTBALL_PREV = {"matches": [
    {"round": "Matchday 38", "date": "2026-05-24", "time": "16:00", "team1": "Arsenal FC",
     "team2": "Burnley FC", "score": [2, 1]},
    {"round": "Matchday 38", "date": "2026-05-24", "time": "16:00", "team1": "Manchester City FC",
     "team2": "Wolverhampton Wanderers FC", "score": [3, 0]},
]}
FD_TEAMS = {"teams": [
    {"id": 57, "name": "Arsenal FC", "shortName": "Arsenal", "tla": "ARS",
     "squad": [{"id": 1, "name": "Bukayo Saka", "position": "Right Winger"},
               {"id": 2, "name": "William Saliba", "position": "Centre-Back"}]},
    {"id": 65, "name": "Manchester City FC", "shortName": "Man City", "tla": "MCI", "squad": []},
    {"id": 1076, "name": "Coventry City FC", "shortName": "Coventry", "tla": "COV", "squad": []},
]}
FD_MATCHES = {"matches": [
    {"utcDate": "2026-08-22T14:00:00Z", "status": "FINISHED", "matchday": 1,
     "homeTeam": {"name": "Arsenal FC"}, "awayTeam": {"name": "Coventry City FC"},
     "score": {"fullTime": {"home": 3, "away": 0}}},
    {"utcDate": "2026-08-29T14:00:00Z", "status": "FINISHED", "matchday": 2,
     "homeTeam": {"name": "Manchester City FC"}, "awayTeam": {"name": "Arsenal FC"},
     "score": {"fullTime": {"home": 2, "away": 1}}},  # openfootball(1-1) 과 불일치
    {"utcDate": "2026-09-05T14:00:00Z", "status": "FINISHED", "matchday": 3,
     "homeTeam": {"name": "Coventry City FC"}, "awayTeam": {"name": "Manchester City FC"},
     "score": {"fullTime": {"home": 0, "away": 4}}},
    {"utcDate": "2026-10-18T13:00:00Z", "status": "TIMED", "matchday": 7,
     "homeTeam": {"name": "Arsenal FC"}, "awayTeam": {"name": "Manchester City FC"},
     "score": {"fullTime": {"home": None, "away": None}}},
]}
FD_SCORERS = {"scorers": [
    {"player": {"id": 1, "name": "Bukayo Saka", "section": "Offence"}, "team": {"id": 57, "name": "Arsenal FC"},
     "goals": 5, "assists": 2, "playedMatches": 6},
]}
AF_FIXTURES = {"errors": [], "response": [
    {"fixture": {"id": 9001, "date": "2026-08-22T14:00:00+00:00", "status": {"short": "FT"}},
     "league": {"round": "Regular Season - 1"}, "teams": {"home": {"name": "Arsenal"}, "away": {"name": "Coventry"}},
     "goals": {"home": 3, "away": 0}},
    {"fixture": {"id": 9002, "date": "2026-08-29T14:00:00+00:00", "status": {"short": "FT"}},
     "league": {"round": "Regular Season - 2"},
     "teams": {"home": {"name": "Manchester City"}, "away": {"name": "Arsenal"}}, "goals": {"home": 2, "away": 1}},
    {"fixture": {"id": 9004, "date": "2026-10-18T13:00:00+00:00", "status": {"short": "NS"}},
     "league": {"round": "Regular Season - 7"},
     "teams": {"home": {"name": "Arsenal"}, "away": {"name": "Manchester City"}}, "goals": {"home": None, "away": None}},
]}
AF_TOPSCORERS = {"errors": [], "response": [
    {"player": {"id": 501, "name": "B. Saka"},
     "statistics": [{"team": {"name": "Arsenal"}, "games": {"position": "Attacker", "minutes": 520, "lineups": 6},
                     "goals": {"total": 5, "assists": 3}}]},
    {"player": {"id": 777, "name": "E. Haaland"},
     "statistics": [{"team": {"name": "Manchester City"}, "games": {"position": "Attacker", "minutes": 540},
                     "goals": {"total": 9, "assists": 1}}]},
]}
AF_INJURIES = {"errors": [], "response": [
    {"player": {"id": 501, "name": "B. Saka", "type": "Missing Fixture", "reason": "Hamstring Injury"},
     "team": {"name": "Arsenal"}, "fixture": {"id": 9004}},
    {"player": {"id": 777, "name": "E. Haaland", "type": "Questionable", "reason": "Knock"},
     "team": {"name": "Manchester City"}, "fixture": {"id": 9004}},
]}
AF_PLAN_ERROR = {"errors": {"plan": "Free plans do not have access to this season"}, "response": []}


def fake_get(url, headers=None, ttl=0):
    if "openfootball" in url:
        if "2026-27" in url:
            return OPENFOOTBALL_NOW
        if "2025-26" in url:
            return OPENFOOTBALL_PREV
    if "football-data.org" in url:
        if "/teams" in url:
            return FD_TEAMS
        if "season=2025" in url:
            raise sources.ApiError("HTTP 403")  # 무료 등급: 직전 시즌 불가
        if "/matches" in url:
            return FD_MATCHES
        if "/scorers" in url:
            return FD_SCORERS
    if "api-sports.io" in url:
        if "season=2025" in url:
            return AF_PLAN_ERROR
        if "/fixtures" in url:
            return AF_FIXTURES
        if "topscorers" in url:
            return AF_TOPSCORERS
        if "topassists" in url:
            return {"errors": [], "response": []}
        if "/injuries" in url:
            return AF_INJURIES
    raise AssertionError(f"예상하지 못한 URL: {url}")


def provider(fd=True, af=True):
    return MultiSourceProvider([
        sources.FootballDataSource(api_key="fd-key" if fd else ""),
        sources.ApiFootballSource(api_key="af-key" if af else ""),
        sources.OpenFootballSource(),
    ], memo_seconds=0)


class TeamNameTest(unittest.TestCase):
    def test_same_club_same_key(self):
        groups = [
            ("Manchester City FC", "Man City", "Manchester City"),
            ("Brighton & Hove Albion FC", "Brighton"),
            ("Wolverhampton Wanderers FC", "Wolves"),
            ("AFC Bournemouth", "Bournemouth"),
            ("Tottenham Hotspur FC", "Spurs", "Tottenham"),
            ("Nottingham Forest FC", "Nott'm Forest"),
        ]
        for names in groups:
            self.assertEqual(len({team_key(n) for n in names}), 1, names)
        self.assertNotEqual(team_key("Manchester United FC"), team_key("Manchester City FC"))

    def test_player_key_matches_initials(self):
        self.assertEqual(player_key("Bukayo Saka"), player_key("B. Saka"))


@mock.patch.object(sources, "cached_get_json", side_effect=fake_get)
class MergeTest(unittest.TestCase):
    def _ctx(self, prov):
        ctx = MatchContext(league=EPL, target_date=TODAY)
        DataCollectorAgent(prov)(ctx)
        DataQualityAgent()(ctx)
        PlayerAnalystAgent()(ctx)
        return ctx

    def test_results_confirmed_and_conflicts(self, _):
        rec = provider().report(EPL, TODAY)["reconciliation"]
        self.assertEqual(rec["results"], 3)
        self.assertEqual(rec["confirmed"], 2)       # 1라운드(3개 출처), 2라운드(fd+af 2-1)
        self.assertEqual(rec["single_source"], 1)   # 3라운드는 football-data 만
        self.assertEqual(len(rec["conflicts"]), 1)  # 2라운드 openfootball 1-1
        self.assertEqual(rec["conflicts"][0]["chosen"], "2-1")

    def test_matches_merged_across_sources(self, _):
        ctx = self._ctx(provider())
        self.assertEqual(len(ctx.fixtures), 1)
        fx = ctx.fixtures[0]
        self.assertEqual(fx.home.name, "Arsenal")
        self.assertEqual(fx.matchday, 7)
        self.assertEqual(fx.utc_date.hour, 13)       # football-data 시각 우선
        self.assertEqual(len(fx.sources), 3)
        # 이번 시즌 3경기 + 직전 시즌 2경기 (football-data 직전 시즌 403, API-Football 플랜 제한은 건너뜀)
        self.assertEqual(len(ctx.finished_matches), 5)
        names = {t.name: t for t in ctx.teams.values()}
        self.assertTrue(names["Coventry City"].promoted)
        self.assertFalse(names["Arsenal"].promoted)
        self.assertFalse(names["Burnley"].current)

    def test_players_merged_with_injuries(self, _):
        ctx = self._ctx(provider())
        ars = next(t for t in ctx.teams.values() if t.name == "Arsenal")
        saka = [p for p in ctx.players[ars.id] if player_key(p.name) == ("b", "saka")]
        self.assertEqual(len(saka), 1)               # 3개 기록(fd 명단·득점, af 득점·부상)이 1명으로
        p = saka[0]
        self.assertEqual((p.name, p.goals, p.assists, p.play_prob), ("Bukayo Saka", 5, 3, 0.0))
        self.assertEqual(p.position, "Offence")
        self.assertLess(ctx.squad_reports[ars.id].attack_factor, 0.9)
        mci = next(t for t in ctx.teams.values() if t.name == "Manchester City")
        self.assertEqual(ctx.players[mci.id][0].play_prob, 0.5)  # Questionable

    def test_status_and_quality_report(self, _):
        ctx = self._ctx(provider())
        by_name = {s["name"]: s for s in ctx.source_report["sources"]}
        self.assertTrue(all(s["ok"] for s in by_name.values()))
        self.assertTrue(any("직전 시즌" in n for n in by_name["football-data.org"]["notes"]))
        self.assertTrue(any("직전 시즌" in n for n in by_name["API-Football"]["notes"]))
        labels = {c["label"]: c["status"] for c in ctx.data_quality["checks"]}
        self.assertEqual(labels["출처 간 스코어 불일치"], "warn")
        self.assertEqual(labels["데이터 출처"], "good")
        self.assertEqual(labels["부상·결장 정보"], "good")

    def test_free_plan_season_block_disables_api_football(self, _):
        def blocked(url, headers=None, ttl=0):
            if "api-sports.io" in url:
                return AF_PLAN_ERROR
            return fake_get(url, headers, ttl)
        with mock.patch.object(sources, "cached_get_json", side_effect=blocked):
            ctx = self._ctx(provider())
        af = next(s for s in ctx.source_report["sources"] if s["name"] == "API-Football")
        self.assertFalse(af["ok"])
        self.assertIn("요금제 제한", af["error"])
        self.assertEqual(ctx.data_source, "football-data.org + openfootball")
        labels = {c["label"]: c["status"] for c in ctx.data_quality["checks"]}
        self.assertEqual(labels["부상·결장 정보"], "warn")
        self.assertFalse(ctx.data_quality["injury_data"])

    def test_keyless_falls_back_to_openfootball(self, _):
        ctx = self._ctx(provider(fd=False, af=False))
        self.assertEqual(ctx.data_source, "openfootball")
        states = {s["name"]: (s["enabled"], s["ok"]) for s in ctx.source_report["sources"]}
        self.assertEqual(states["football-data.org"], (False, False))
        self.assertEqual(states["openfootball"], (True, True))
        self.assertEqual(len(ctx.finished_matches), 4)  # 3라운드 결과 없음
        labels = {c["label"]: c["status"] for c in ctx.data_quality["checks"]}
        self.assertEqual(labels["선수·부상 데이터"], "bad")

    def test_all_sources_failing_raises(self, _):
        with mock.patch.object(sources, "cached_get_json", side_effect=sources.ApiError("down")):
            with self.assertRaises(sources.ApiError):
                provider(fd=False, af=False).get_matches(EPL, TODAY)


class TimezoneTest(unittest.TestCase):
    def test_uk_summer_time(self):
        self.assertEqual(sources._uk_offset_hours(date(2026, 8, 22)), 1)
        self.assertEqual(sources._uk_offset_hours(date(2026, 12, 26)), 0)
        self.assertEqual(sources._uk_offset_hours(date(2026, 10, 24)), 1)  # 10월 마지막 일요일(25일) 전
        self.assertEqual(sources._uk_offset_hours(date(2026, 10, 25)), 0)


if __name__ == "__main__":
    unittest.main()
