"""전역 설정: 지원 리그, 모델 파라미터, 경로."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
OUTPUT_DIR = ROOT_DIR / "output"


def _load_dotenv(path: Path) -> None:
    """프로젝트 루트의 .env (KEY=VALUE) 를 환경변수로 읽는다. 이미 설정된 값은 덮어쓰지 않음."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(ROOT_DIR / ".env")


@dataclass(frozen=True)
class League:
    key: str              # 내부 키 (CLI / 웹에서 사용)
    name: str             # 표시 이름
    api_code: str         # football-data.org 대회 코드
    country: str
    api_football_id: int  # API-Football 리그 id
    openfootball_code: str  # openfootball 파일명 (예: en.1)


ALL_LEAGUES: dict[str, League] = {
    "EPL": League("EPL", "Premier League", "PL", "England", 39, "en.1"),
    "LALIGA": League("LALIGA", "La Liga", "PD", "Spain", 140, "es.1"),
    "BUNDESLIGA": League("BUNDESLIGA", "Bundesliga", "BL1", "Germany", 78, "de.1"),
}

# 현재는 프리미어리그만 활성화. 리그 키를 추가하면 CLI/웹에 바로 노출된다.
ENABLED_LEAGUES = ("EPL",)
LEAGUES: dict[str, League] = {k: ALL_LEAGUES[k] for k in ENABLED_LEAGUES}


def get_league(key: str) -> League:
    try:
        return LEAGUES[key.upper()]
    except KeyError as exc:
        raise ValueError(f"지원하지 않는 리그: {key} (가능: {', '.join(LEAGUES)})") from exc


@dataclass(frozen=True)
class ModelConfig:
    # 팀 분석
    time_decay_xi: float = 0.005      # 경기 가중치 = exp(-xi * 경과일수)
    shrinkage_games: float = 6.0      # 시즌 초반 표본 부족 시 리그 평균(1.0)으로 당기는 강도
    form_window: int = 5              # 최근 폼 계산 경기 수
    form_weight: float = 0.08         # 폼이 기대득점에 미치는 최대 영향 (±8%)
    promoted_attack_prior: float = 0.85   # 승격팀 공격력 사전값 (리그 평균 = 1.0)
    promoted_defense_prior: float = 1.15  # 승격팀 수비력 사전값 (1보다 크면 실점 많음)
    # 선수 분석
    absence_attack_impact: float = 0.6   # 결장 선수 득점 기여 중 실제로 손실되는 비율
    absence_defense_impact: float = 0.04 # 주전 수비/GK 1명 결장 시 실점 증가율
    # 예측
    dixon_coles_rho: float = -0.10    # 저득점(0-0, 1-0, 0-1, 1-1) 보정 계수
    draw_gap: float = 0.10            # 홈승·원정승 확률 차이가 이보다 작고
    draw_min: float = 0.25            # 무승부 확률이 이 이상이면 무승부로 예측
    max_goals: int = 10


MODEL = ModelConfig()

FOOTBALL_DATA_API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY", "").strip()
FOOTBALL_DATA_BASE_URL = "https://api.football-data.org/v4"
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY", "").strip()
API_FOOTBALL_BASE_URL = "https://v3.football.api-sports.io"
OPENFOOTBALL_URL = "https://raw.githubusercontent.com/openfootball/football.json/master/{season}/{code}.json"

# 현지 매체 예측 수집 (Claude + 웹 검색)
MEDIA_FILE = DATA_DIR / "media" / "media.json"
MEDIA_MODEL = os.environ.get("MEDIA_MODEL", "claude-opus-5-5")
MEDIA_EFFORT = os.environ.get("MEDIA_EFFORT", "medium")
# 검색 허용 도메인: Claude(Anthropic)의 접근을 허용하는 영국 축구 매체만.
# BBC, Guardian, Telegraph, Times, Independent, Mail, Mirror, Standard, Sun, Express, talkSPORT, Reach 지역지,
# The Athletic(nytimes.com)은 AI 접근을 막아 두었으므로(2026-10 확인) 넣지 않는다 — 넣으면 웹 검색 요청이 400 으로 실패.
MEDIA_DOMAINS = [
    # 경기마다 스코어 예측을 내는 영국 매체
    "sportsmole.co.uk", "whoscored.com", "squawka.com", "skysports.com", "sportinglife.com",
    # 프리뷰·칼럼에 예측이 실리는 영국 매체
    "espn.co.uk", "eurosport.co.uk", "fourfourtwo.com", "football365.com", "planetfootball.com",
    "givemesport.com", "90min.com", "teamtalk.com", "thesportsman.com", "goal.com",
]


@dataclass(frozen=True)
class MediaConfig:
    max_calls_per_run: int = 3        # 한 번 실행에 Claude 호출 상한 (비용 제한)
    max_searches_per_call: int = 12   # 호출 한 번당 웹 검색 상한
    lookahead_hours: int = 72         # 이만큼 남은 경기부터 수집
    refresh_hours: int = 24           # 이 시간이 지나면 킥오프 전 한 번 더 수집
    backfill_days: int = 21           # 적중률 계산용으로 지난 경기도 수집하는 기간
    max_blend: float = 0.30           # 매체 합의가 승무패 확률에 반영되는 최대 비율
    blend_half_weight: float = 3.0    # 가중치 합이 이 값일 때 최대 비율의 절반 반영
    prior_hit_rate: float = 0.5       # 적중률 사전값 (기록 적은 칼럼니스트)
    prior_games: float = 6.0          # 사전값 강도 (가상의 경기 수)


MEDIA = MediaConfig()
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "3600"))
