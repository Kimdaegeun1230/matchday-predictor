"""CoordinatorAgent: 리그·날짜별로 에이전트 팀을 순서대로 실행하는 총괄 에이전트.

    DataCollector ─▶ DataQuality ─▶ TeamAnalyst ─▶ PlayerAnalyst ─▶ Predictor ─▶ Reporter
"""
from __future__ import annotations

import logging
import time
from datetime import date

from ..config import LEAGUES, get_league
from ..providers.base import DataProvider
from ..providers.multi import MultiSourceProvider
from ..providers.sample import SampleProvider
from .base import Agent, MatchContext
from .data_agent import DataCollectorAgent
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
    def __init__(self, source: str = "auto", write_files: bool = True, cache_seconds: int = 600):
        self.source = source
        self.provider = make_provider(source)
        self.write_files = write_files
        self.cache_seconds = cache_seconds
        self._cache: dict[tuple[str, date], tuple[float, dict]] = {}

    def build_team(self) -> list[Agent]:
        return [
            DataCollectorAgent(self.provider),
            DataQualityAgent(),
            TeamAnalystAgent(),
            PlayerAnalystAgent(),
            PredictionAgent(),
            ReportAgent(write_file=self.write_files),
        ]

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

    def source_status(self, league_key: str, as_of: date) -> dict:
        """출처별 응답 상태와 병합 결과 (샘플 공급자는 빈 값)."""
        report = getattr(self.provider, "report", None)
        return report(get_league(league_key), as_of) if report else {}

    def predict_all(self, target_date: date) -> dict[str, dict]:
        return {k: self.predict(k, target_date) for k in LEAGUES}
