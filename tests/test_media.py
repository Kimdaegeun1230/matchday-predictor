"""현지 매체 예측: 수집기(가짜 Claude 응답), 적중률, 예측 반영을 네트워크·비용 없이 검증."""
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from football_predictor.agents.base import MatchContext
from football_predictor.agents.media_agent import MediaConsensusAgent
from football_predictor.agents.prediction_agent import PredictionAgent
from football_predictor.agents.team_agent import TeamAnalystAgent
from football_predictor.config import MEDIA, get_league
from football_predictor.media.collector import MediaCollector
from football_predictor.media.store import MediaStore, PunditStats
from football_predictor.models import Match, Team

UTC = timezone.utc


def block(**kw):
    return SimpleNamespace(**kw)


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def fake_client(responses):
    msgs = FakeMessages(responses)
    return SimpleNamespace(beta=SimpleNamespace(messages=msgs)), msgs


def response(stop, content):
    return SimpleNamespace(stop_reason=stop, content=content,
                           usage=SimpleNamespace(input_tokens=100, output_tokens=50,
                                                 server_tool_use=SimpleNamespace(web_search_requests=2)))


FIXTURES = [{"id": "11", "home": "Arsenal", "away": "Chelsea", "kickoff": "2026-10-18T14:00+00:00"}]


class CollectorTest(unittest.TestCase):
    def test_pause_then_record(self):
        record = block(type="tool_use", name="record_predictions", input={"predictions": [
            {"fixture_id": "11", "outlet": "BBC Sport", "author": "Chris Sutton", "home_goals": 2, "away_goals": 1,
             "url": "https://www.bbc.co.uk/sport/football/articles/x", "headline": "Sutton's predictions"},
            {"fixture_id": "11", "outlet": "Fake", "author": "Nobody", "home_goals": 0, "away_goals": 0,
             "url": "https://example.com/made-up", "headline": "not allowed"},       # 허용 매체 아님 → 버림
            {"fixture_id": "99", "outlet": "BBC Sport", "author": "X", "home_goals": 1, "away_goals": 1,
             "url": "https://www.bbc.co.uk/a", "headline": "other match"},           # 모르는 경기 → 버림
        ]})
        client, msgs = fake_client([
            response("pause_turn", [block(type="text", text="searching...")]),
            response("tool_use", [record]),
        ])
        c = MediaCollector(client=client)
        out = c.collect(FIXTURES)
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]["author"], out[0]["home_goals"], out[0]["away_goals"]), ("Chris Sutton", 2, 1))
        self.assertEqual(len(msgs.calls), 2)
        first = msgs.calls[0]
        self.assertEqual(first["model"], "claude-opus-5-5")
        self.assertEqual(first["fallbacks"], "default")
        self.assertEqual(first["tools"][0]["type"], "web_search_20260209")
        self.assertIn("bbc.co.uk", first["tools"][0]["allowed_domains"])
        self.assertEqual(first["tools"][1]["input_schema"]["properties"]["predictions"]["items"]
                         ["properties"]["fixture_id"]["enum"], ["11"])
        self.assertEqual(msgs.calls[1]["messages"][-1]["role"], "assistant")  # 멈춘 지점부터 이어서
        self.assertEqual(c.usage["calls"], 2)
        self.assertEqual(c.usage["web_searches"], 4)

    def test_refusal_returns_nothing(self):
        client, _ = fake_client([response("refusal", [])])
        self.assertEqual(MediaCollector(client=client).collect(FIXTURES), [])

    def test_end_turn_without_tool_nudges_once(self):
        record = block(type="tool_use", name="record_predictions", input={"predictions": []})
        client, msgs = fake_client([response("end_turn", [block(type="text", text="done")]),
                                    response("tool_use", [record])])
        self.assertEqual(MediaCollector(client=client).collect(FIXTURES), [])
        self.assertIn("record_predictions", msgs.calls[1]["messages"][-1]["content"])


def team(name, tid):
    return Team(tid, name, name, name[:3].upper())


def match(mid, h, a, when, hg=None, ag=None):
    return Match(mid, when, h, a, "FINISHED" if hg is not None else "SCHEDULED", 1, hg, ag)


def pred(fid, author, hg, ag, outlet="BBC Sport"):
    return {"fixture_id": str(fid), "home": "", "away": "", "kickoff": "", "outlet": outlet, "author": author,
            "home_goals": hg, "away_goals": ag, "url": "https://www.bbc.co.uk/x", "headline": "", "collected_at": ""}


class StoreTest(unittest.TestCase):
    def test_hit_rate_shrinks_to_prior(self):
        s = PunditStats("BBC", "A", games=2, outcome_hits=2)
        self.assertLess(s.hit_rate(), 1.0)
        self.assertGreater(s.hit_rate(), MEDIA.prior_hit_rate)
        good = PunditStats("BBC", "B", games=30, outcome_hits=21)
        bad = PunditStats("BBC", "C", games=30, outcome_hits=9)
        self.assertGreater(good.weight(), 1.0)
        self.assertLess(bad.weight(), 1.0)

    def test_due_fixtures_and_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MediaStore(Path(tmp) / "media.json")
            now = datetime(2026, 10, 18, 9, tzinfo=UTC)
            ars, che, liv = team("Arsenal", 1), team("Chelsea", 2), team("Liverpool", 3)
            upcoming = match(10, ars, che, now + timedelta(hours=5))
            far = match(12, ars, liv, now + timedelta(days=6))
            past = match(13, liv, che, now - timedelta(days=3), 2, 0)
            old = match(14, che, liv, now - timedelta(days=60), 1, 1)
            due = store.due_fixtures([upcoming, far, past, old], now)
            self.assertEqual({m.id for m in due}, {10, 13})
            groups = store.group_by_day(due)
            self.assertEqual(groups[0][0].id, 10)   # 다가오는 경기 먼저

            store.add(["13"], [pred(13, "Sutton", 2, 1), pred(13, "Guest", 0, 1)], when=now)
            store.save()
            again = MediaStore(store.path)
            self.assertEqual(len(again.predictions), 2)
            self.assertEqual({m.id for m in again.due_fixtures([past], now)}, set())  # 이미 수집함
            stats = again.pundit_stats({"13": (2, 0)})
            self.assertEqual(stats["bbc sport|sutton"].outcome_hits, 1)
            self.assertEqual(stats["bbc sport|guest"].outcome_hits, 0)


class ConsensusTest(unittest.TestCase):
    def _ctx(self, tmp, media):
        ars, che = team("Arsenal", 1), team("Chelsea", 2)
        ctx = MatchContext(league=get_league("EPL"), target_date=date(2026, 10, 18))
        ctx.teams = {1: ars, 2: che}
        base = datetime(2026, 8, 1, 15, tzinfo=UTC)
        # 과거 경기: Sutton 은 3번 다 맞힘, Guest 는 3번 다 틀림
        for k in range(3):
            ctx.finished_matches.append(match(100 + k, ars, che, base + timedelta(days=7 * k), 2, 1))
        fx = match(200, ars, che, datetime(2026, 10, 18, 14, tzinfo=UTC))
        ctx.fixtures = [fx]
        path = Path(tmp) / "media.json"
        path.write_text(json.dumps({"predictions": media, "collected": {}}), encoding="utf-8")
        TeamAnalystAgent()(ctx)
        PredictionAgent()(ctx)
        return ctx, path

    def test_away_consensus_shifts_probabilities(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = [pred(100 + k, "Sutton", 1, 0) for k in range(3)] + \
                    [pred(100 + k, "Guest", 0, 2) for k in range(3)] + \
                    [pred(200, "Sutton", 0, 2), pred(200, "Guest", 0, 2), pred(200, "Lawro", 1, 3)]
            ctx, path = self._ctx(tmp, media)
            before = ctx.predictions[0].away_win
            MediaConsensusAgent(path)(ctx)
            p = ctx.predictions[0]
            self.assertGreater(p.away_win, before)
            self.assertAlmostEqual(p.home_win + p.draw + p.away_win, 1.0, places=3)
            self.assertEqual(p.media["count"], 3)
            self.assertLessEqual(p.media["blend"], MEDIA.max_blend)
            weights = {it["author"]: it["weight"] for it in p.media["items"]}
            self.assertGreater(weights["Sutton"], weights["Lawro"])  # 기록 좋은 사람 > 기록 없는 사람
            self.assertGreater(weights["Lawro"], weights["Guest"])   # 기록 없는 사람 > 계속 틀린 사람
            self.assertEqual(ctx.media_report["leaderboard"][0]["author"], "Sutton")

    def test_no_media_file_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx, _ = self._ctx(tmp, [])
            before = ctx.predictions[0].home_win
            MediaConsensusAgent(Path(tmp) / "missing.json")(ctx)
            self.assertEqual(ctx.predictions[0].home_win, before)
            self.assertFalse(ctx.media_report["available"])


if __name__ == "__main__":
    unittest.main()
