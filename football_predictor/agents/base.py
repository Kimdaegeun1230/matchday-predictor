"""에이전트 공통 인터페이스와 공유 작업 공간(Blackboard).

각 에이전트는 MatchContext를 읽고, 자기 담당 결과만 써 넣는다.
에이전트끼리 직접 호출하지 않으므로 하나를 교체해도 나머지는 그대로 동작한다.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..config import League
from ..models import Match, Player, Prediction, SquadReport, Team, TeamStrength


@dataclass
class MatchContext:
    league: League
    target_date: date

    # DataCollectorAgent
    teams: dict[int, Team] = field(default_factory=dict)
    finished_matches: list[Match] = field(default_factory=list)
    fixtures: list[Match] = field(default_factory=list)
    players: dict[int, list[Player]] = field(default_factory=dict)
    all_matches: list[Match] = field(default_factory=list)   # 기준일 이후 포함 전체
    data_source: str = ""
    source_report: dict = field(default_factory=dict)        # 출처별 상태 + 병합 결과

    # DataQualityAgent
    data_quality: dict = field(default_factory=dict)

    # TeamAnalystAgent
    league_avg_home_goals: float = 1.5
    league_avg_away_goals: float = 1.2
    team_strengths: dict[int, TeamStrength] = field(default_factory=dict)
    standings: list[dict] = field(default_factory=list)

    # PlayerAnalystAgent
    squad_reports: dict[int, SquadReport] = field(default_factory=dict)

    # PredictionAgent
    predictions: list[Prediction] = field(default_factory=list)

    # 실행 기록
    log: list[dict[str, Any]] = field(default_factory=list)


class Agent(ABC):
    """모든 에이전트의 기반 클래스."""

    name: str = "agent"
    role: str = ""

    def __init__(self) -> None:
        self.logger = logging.getLogger(f"agent.{self.name}")

    def __call__(self, ctx: MatchContext) -> MatchContext:
        start = time.perf_counter()
        self.logger.info("시작: %s", self.role)
        summary = self.run(ctx)
        elapsed = round(time.perf_counter() - start, 3)
        ctx.log.append({"agent": self.name, "role": self.role, "seconds": elapsed, "summary": summary})
        self.logger.info("완료 (%.3fs): %s", elapsed, summary)
        return ctx

    @abstractmethod
    def run(self, ctx: MatchContext) -> str:
        """ctx를 갱신하고 한 줄 요약을 반환한다."""
