"""데이터 공급자 인터페이스. 실제 API든 샘플이든 같은 형태로 돌려준다."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from ..config import League
from ..models import Match, Player, Team


class DataProvider(ABC):
    name: str = "provider"

    @abstractmethod
    def get_teams(self, league: League) -> dict[int, Team]:
        ...

    @abstractmethod
    def get_matches(self, league: League, as_of: date) -> list[Match]:
        """현재 시즌(가능하면 직전 시즌 포함)의 전체 경기 목록. as_of는 기준일."""

    @abstractmethod
    def get_players(self, league: League, teams: dict[int, Team]) -> dict[int, list[Player]]:
        ...
