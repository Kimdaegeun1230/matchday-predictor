"""CoordinatorAgent: 리그·날짜별로 에이전트 팀을 순서대로 실행하는 총괄 에이전트.

    DataCollector ─▶ DataQuality ─▶ TeamAnalyst ─▶ PlayerAnalyst ─▶ Predictor ─▶ MediaConsensus ─▶ Reporter

현지 매체 예측 수집(collect_media)은 비용이 드는 Claude 호출이라 예측 파이프라인과 따로 실행한다.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date
from pathlib import Path

from ..config import LEAGUES, MEDIA, MEDIA_FILE, get_league
from ..media.store import MediaStore
from ..providers.base import DataProvider
from ..providers.multi import MultiSourceProvider
from ..providers.sample import SampleProvider
from .base import Agent, MatchContext
from .data_agent import DataCollectorAgent
from .media_agent import MediaConsensusAgent
from .player_agent import PlayerAnalystAgent
from .prediction_agent import PredictionAgent
from .quality_agent import DataQualityAgent
from .report_agent import ReportAgent, build_report
from .team_agent import TeamAnalystAgent

log = logging.getLogger("agent.coordinator")


SOURCES = ("auto", "sample")


def make_provider(source: str = "auto") -> DataProvider:
    """source: auto(football-data + API-Football + openfootball 병합, 키 없는 출처는 건너뜀) | sample(가상)"""
    if source == "auto":
        return MultiSourceProvider()
    if source == "sample":
        return SampleProvider()
    raise ValueError(f"알 수 없는 데이터 소스: {source} (가능: {', '.join(SOURCES)})")


class CoordinatorAgent:
    def __init__(self, source: str = "auto", write_files: bool = True, cache_seconds: int = 600,
                 media_path: Path = MEDIA_FILE):
        self.source = source
        self.provider = make_provider(source)
        self.write_files = write_files
        self.cache_seconds = cache_seconds
        self.media_path = media_path
        self._cache: dict[tuple[str, date], tuple[float, dict]] = {}

    def build_team(self) -> list[Agent]:
        return [
            DataCollectorAgent(self.provider),
            DataQualityAgent(),
            TeamAnalystAgent(),
            PlayerAnalystAgent(),
            PredictionAgent(),
            MediaConsensusAgent(self.media_path),
            ReportAgent(write_file=self.write_files),
        ]

    def collect_media(self, league_key: str, collector=None, max_calls: int | None = None) -> dict:
        """수집할 때가 된 경기만 골라 Claude 로 현지 매체 예측을 모은다 (실행당 호출 수 제한)."""
        if collector is None and not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            return {"skipped": "ANTHROPIC_API_KEY 없음", "calls": 0, "found": 0}
        league = get_league(league_key)
        store = MediaStore(self.media_path)
        matches = self.provider.get_matches(league, date.today())
        groups = store.group_by_day(store.due_fixtures(matches))[: max_calls or MEDIA.max_calls_per_run]
        if not groups:
            return {"calls": 0, "found": 0, "due": 0}
        if collector is None:
            from ..media.collector import MediaCollector
            collector = MediaCollector()
        found = 0
        for group in groups:
            fixtures = [{"id": str(m.id), "home": m.home.name, "away": m.away.name,
                         "kickoff": m.utc_date.isoformat(timespec="minutes")} for m in group]
            preds = collector.collect(fixtures)
            store.add([f["id"] for f in fixtures], preds)
            store.save()  # 호출마다 저장 — 중간에 실패해도 앞의 결과는 남김
            found += len(preds)
            log.info("%s %d경기 → 매체 예측 %d건", group[0].utc_date.date(), len(group), len(preds))
        self._cache.clear()
        return {"calls": len(groups), "found": found, "matches": sum(map(len, groups)),
                "usage": getattr(collector, "usage", {})}

    def predict(self, league_key: str, target_date: date, use_cache: bool = True) -> dict:
        league = get_league(league_key)
        key = (league.key, target_date)
        hit = self._cache.get(key)
        if use_cache and hit and time.time() - hit[0] < self.cache_seconds:
            return hit[1]

        ctx = MatchContext(league=league, target_date=target_date)
        for agent in self.build_team():
            agent(ctx)
        report = build_report(ctx)
        self._cache[key] = (time.time(), report)
        return report

    def next_match_date(self, league_key: str, from_date: date) -> date | None:
        """from_date 이후(당일 포함) 아직 끝나지 않은 첫 경기의 날짜."""
        league = get_league(league_key)
        upcoming = [m.utc_date.date() for m in self.provider.get_matches(league, from_date)
                    if not m.finished and m.status != "POSTPONED" and m.utc_date.date() >= from_date]
        return min(upcoming, default=None)

    def match_calendar(self, league_key: str, from_date: date) -> list[dict]:
        """이번 시즌 경기일 목록: [{date, matchday, matches, finished}]"""
        league = get_league(league_key)
        season_year = from_date.year if from_date.month >= 7 else from_date.year - 1
        days: dict[date, dict] = {}
        for m in self.provider.get_matches(league, from_date):
            d = m.utc_date.date()
            if d < date(season_year, 7, 1):
                continue
            row = days.setdefault(d, {"date": d.isoformat(), "matchday": m.matchday, "matches": 0, "finished": 0})
            row["matches"] += 1
            row["finished"] += int(m.finished)
        return [days[d] for d in sorted(days)]

    def round_accuracy(self, league_key: str, today: date | None = None) -> dict:
        """시즌 경기일마다 그날 기준 예측을 만들어 실제 결과와 비교한 라운드별 적중률."""
        from ..evaluation import round_accuracy
        today = today or date.today()
        reports = [self.predict(league_key, date.fromisoformat(d["date"]))
                   for d in self.match_calendar(league_key, today) if d["finished"]]
        return round_accuracy(reports)

    def source_status(self, league_key: str, as_of: date) -> dict:
        """출처별 응답 상태와 병합 결과 (샘플 공급자는 빈 값)."""
        report = getattr(self.provider, "report", None)
        return report(get_league(league_key), as_of) if report else {}

    def predict_all(self, target_date: date) -> dict[str, dict]:
        return {k: self.predict(k, target_date) for k in LEAGUES}
