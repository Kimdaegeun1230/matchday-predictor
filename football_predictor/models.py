"""에이전트들이 주고받는 도메인 데이터 모델."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Team:
    id: int
    name: str
    short_name: str = ""
    tla: str = ""
    crest: str = ""
    promoted: bool = False      # 승격팀: 직전 시즌 1부 기록 없음 → 약한 사전값에서 출발
    current: bool = True        # 이번 시즌 리그 소속 (False = 직전 시즌에만 있던 강등팀)


@dataclass
class Player:
    id: int
    name: str
    team_id: int
    position: str = ""          # Goalkeeper / Defence / Midfield / Offence
    goals: int = 0
    assists: int = 0
    played: int = 0             # 선발 또는 출전 경기 수
    minutes: int = 0
    xg: float = 0.0             # 시즌 누적 기대득점
    xa: float = 0.0             # 시즌 누적 기대도움
    play_prob: float = 1.0      # 다음 경기 출전 확률 (0 = 확정 결장)
    absence_reason: str = ""    # injury / suspension / 뉴스 원문

    @property
    def available(self) -> bool:
        return self.play_prob >= 0.5


@dataclass
class Match:
    id: int
    utc_date: datetime
    home: Team
    away: Team
    status: str                 # SCHEDULED / TIMED / FINISHED ...
    matchday: Optional[int] = None
    home_goals: Optional[int] = None
    away_goals: Optional[int] = None
    sources: list[str] = field(default_factory=list)       # 이 경기를 제공한 출처
    confirmed_by: list[str] = field(default_factory=list)  # 채택된 결과와 같은 스코어를 준 출처

    @property
    def finished(self) -> bool:
        return self.status == "FINISHED" and self.home_goals is not None


@dataclass
class TeamStrength:
    team_id: int
    attack_home: float = 1.0
    defense_home: float = 1.0   # 1보다 크면 실점이 많은(약한) 수비
    attack_away: float = 1.0
    defense_away: float = 1.0
    form_ppg: float = 0.0       # 최근 경기 평균 승점
    form: str = ""              # 최근 결과, 오래된 것 → 최신 순 (예: "WDLWW")
    games: int = 0

    @property
    def rating(self) -> float:
        """종합 전력: 공격 / 수비 (1.0 = 리그 평균, 클수록 강함)."""
        return (self.attack_home + self.attack_away) / (self.defense_home + self.defense_away)


@dataclass
class SquadReport:
    team_id: int
    attack_factor: float = 1.0
    defense_factor: float = 1.0
    missing_players: list[dict] = field(default_factory=list)  # {name, position, play_prob, reason}
    key_players: list[dict] = field(default_factory=list)      # {name, position, goals, assists, xg}


@dataclass
class Prediction:
    match_id: int
    league: str
    kickoff: str
    matchday: Optional[int]
    home_team: str
    away_team: str
    home_tla: str
    away_tla: str
    status: str                 # SCHEDULED / IN_PLAY / FINISHED
    actual_score: Optional[str]  # 이미 끝난 경기의 실제 스코어
    home_xg: float
    away_xg: float
    predicted_score: str        # 최빈 스코어 (가장 유력한 결과 안에서 확률이 가장 높은 스코어)
    xg_score: str               # 기대득점 기준 스코어 (반올림, 결과와 일치하게 보정)
    home_win: float
    draw: float
    away_win: float
    over_2_5: float
    btts: float
    top_scores: list[dict]
    confidence: str
    score_grid: list[list[float]] = field(default_factory=list)  # [홈골][원정골] 확률, 0~5골
    home_squad: dict = field(default_factory=dict)
    away_squad: dict = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    confirmed_by: list[str] = field(default_factory=list)
    media: dict = field(default_factory=dict)  # 현지 매체 예측과 반영 내용 (MediaConsensusAgent)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
