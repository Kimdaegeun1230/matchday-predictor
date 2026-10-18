"""출처별 어댑터. 각 출처의 응답을 같은 형태(SourceData)로 바꾼다.

| 출처            | 약관              | 이번 시즌 일정/결과 | 직전 시즌 | 선수/부상          |
|-----------------|-------------------|---------------------|-----------|--------------------|
| football-data   | 무료 키, 약관 有  | O (주 출처)         | 등급에 따라 | 명단·득점자(등급에 따라) |
| API-Football    | 무료 키, 약관 有  | O (교차 확인)       | O         | 부상·득점·도움     |
| openfootball    | 퍼블릭 도메인     | O (반영 느림)       | O         | X                  |
| FPL (비공식)    | 공식 약관 없음    | X (쓰지 않음)       | X         | 부상·결장·출전확률 |

키가 없거나 요청이 막힌 출처는 건너뛰고, 그 사실을 status 에 남긴다.
FPL 은 공개 문서·이용약관이 없는 비공식 API 라, 일정·결과에는 쓰지 않고 부상·결장 보강에만 쓴다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from ..config import (API_FOOTBALL_BASE_URL, API_FOOTBALL_KEY, CACHE_TTL_SECONDS, FOOTBALL_DATA_API_KEY,
                      FOOTBALL_DATA_BASE_URL, OPENFOOTBALL_URL, League)
from .http import ApiError, cached_get_json
from .teamnames import team_key

log = logging.getLogger(__name__)
DAY = 24 * 3600


@dataclass
class RawTeam:
    key: str
    name: str
    tla: str = ""


@dataclass
class RawMatch:
    utc_date: datetime
    home: str               # team_key
    away: str
    status: str             # FINISHED / SCHEDULED / IN_PLAY / POSTPONED
    home_goals: int | None = None
    away_goals: int | None = None
    matchday: int | None = None


@dataclass
class RawPlayer:
    team: str               # team_key
    name: str
    source_id: str = ""
    position: str = ""      # Goalkeeper / Defence / Midfield / Offence
    goals: int = 0
    assists: int = 0
    played: int = 0
    minutes: int = 0
    play_prob: float = 1.0
    reason: str = ""
    aliases: list[str] = field(default_factory=list)  # 다른 표기 (예: FPL web_name "Saka")


@dataclass
class SourceData:
    name: str
    teams: list[RawTeam] = field(default_factory=list)
    matches: list[RawMatch] = field(default_factory=list)       # 이번 시즌
    prev_matches: list[RawMatch] = field(default_factory=list)  # 직전 시즌
    players: list[RawPlayer] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def season_start_year(d: date) -> int:
    return d.year if d.month >= 7 else d.year - 1


def normalize_position(text: str) -> str:
    t = (text or "").lower()
    if "goal" in t or t == "g":
        return "Goalkeeper"
    if "def" in t or "back" in t or t == "d":
        return "Defence"
    if "mid" in t or t == "m":
        return "Midfield"
    if any(w in t for w in ("off", "att", "forw", "wing", "striker")) or t in ("f", "a"):
        return "Offence"
    return ""


class Source:
    key = "source"
    name = "source"
    url = ""
    license = ""
    attribution = ""
    needs_key = False

    def __init__(self, ttl: int = CACHE_TTL_SECONDS):
        self.ttl = ttl

    @property
    def enabled(self) -> bool:
        return True

    def fetch(self, league: League, as_of: date) -> SourceData:
        raise NotImplementedError


# ---------------------------------------------------------------- openfootball
def _uk_offset_hours(d: date) -> int:
    """영국 서머타임(BST): 3월 마지막 일요일 ~ 10월 마지막 일요일 → UTC+1."""
    def last_sunday(year: int, month: int) -> date:
        nxt = date(year + (month == 12), month % 12 + 1, 1)
        last = nxt - timedelta(days=1)
        return last - timedelta(days=(last.weekday() + 1) % 7)
    return 1 if last_sunday(d.year, 3) <= d < last_sunday(d.year, 10) else 0


class OpenFootballSource(Source):
    key = "openfootball"
    name = "openfootball"
    url = "https://github.com/openfootball/football.json"
    license = "퍼블릭 도메인 (사용 제한 없음)"
    attribution = "openfootball/football.json"

    def _season(self, league: League, start: int, ttl: int) -> list[RawMatch]:
        season = f"{start}-{str(start + 1)[2:]}"
        data = cached_get_json(OPENFOOTBALL_URL.format(season=season, code=league.openfootball_code), ttl=ttl)
        out = []
        for m in data.get("matches", []):
            d = date.fromisoformat(m["date"])
            hh, mm = (m.get("time") or "15:00").split(":")[:2]
            local = datetime(d.year, d.month, d.day, int(hh), int(mm), tzinfo=timezone.utc)
            utc = local - timedelta(hours=_uk_offset_hours(d))
            # 점수 형식이 두 가지: {"ft": [h, a]} 또는 [h, a]
            score = m.get("score")
            ft = score.get("ft") if isinstance(score, dict) else score
            done = isinstance(ft, list) and len(ft) == 2
            md = "".join(ch for ch in str(m.get("round", "")) if ch.isdigit())
            out.append(RawMatch(utc, team_key(m["team1"]), team_key(m["team2"]),
                                "FINISHED" if done else "SCHEDULED",
                                int(ft[0]) if done else None, int(ft[1]) if done else None,
                                int(md) if md else None))
        return out

    def fetch(self, league: League, as_of: date) -> SourceData:
        start = season_start_year(as_of)
        sd = SourceData(self.name)
        sd.matches = self._season(league, start, self.ttl)
        try:
            sd.prev_matches = self._season(league, start - 1, self.ttl * 24)
        except ApiError as exc:
            sd.notes.append(f"직전 시즌 없음: {exc}")
        names = {}
        for m in sd.matches:
            names.setdefault(m.home, m.home)
            names.setdefault(m.away, m.away)
        sd.teams = [RawTeam(k, k) for k in names]
        return sd


# ---------------------------------------------------------------- football-data.org
_FD_STATUS = {"FINISHED": "FINISHED", "AWARDED": "FINISHED", "IN_PLAY": "IN_PLAY", "PAUSED": "IN_PLAY",
              "LIVE": "IN_PLAY", "POSTPONED": "POSTPONED", "SUSPENDED": "POSTPONED", "CANCELLED": "POSTPONED"}


class FootballDataSource(Source):
    key = "football-data"
    name = "football-data.org"
    url = "https://www.football-data.org"
    license = "무료 등급 이용약관 (키 필요)"
    attribution = "Football data provided by the Football-Data.org API"
    needs_key = True

    def __init__(self, api_key: str = FOOTBALL_DATA_API_KEY, ttl: int = CACHE_TTL_SECONDS):
        super().__init__(ttl)
        self.api_key = api_key

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, ttl: int | None = None):
        return cached_get_json(f"{FOOTBALL_DATA_BASE_URL}{path}", {"X-Auth-Token": self.api_key}, ttl or self.ttl)

    def _matches(self, raw: dict) -> list[RawMatch]:
        out = []
        for m in raw.get("matches", []):
            if not (m.get("homeTeam") or {}).get("name"):
                continue
            ft = (m.get("score") or {}).get("fullTime") or {}
            status = _FD_STATUS.get(m.get("status", ""), "SCHEDULED")
            done = status == "FINISHED" and ft.get("home") is not None
            out.append(RawMatch(
                datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")),
                team_key(m["homeTeam"]["name"]), team_key(m["awayTeam"]["name"]),
                status if done or status != "FINISHED" else "SCHEDULED",
                ft.get("home") if done else None, ft.get("away") if done else None, m.get("matchday")))
        return out

    def fetch(self, league: League, as_of: date) -> SourceData:
        sd = SourceData(self.name)
        start = season_start_year(as_of)
        teams = self._get(f"/competitions/{league.api_code}/teams?season={start}", ttl=DAY)
        key_by_id = {}
        for t in teams.get("teams", []):
            k = team_key(t["name"])
            key_by_id[t["id"]] = k
            sd.teams.append(RawTeam(k, t.get("shortName") or t["name"], t.get("tla") or ""))
            for p in t.get("squad") or []:
                sd.players.append(RawPlayer(k, p.get("name", ""), f"fd:{p.get('id')}", normalize_position(p.get("position", ""))))
        if teams.get("teams") and not sd.players:
            sd.notes.append("선수 명단(squad) 미제공 등급")

        sd.matches = self._matches(self._get(f"/competitions/{league.api_code}/matches?season={start}"))
        try:
            sd.prev_matches = self._matches(self._get(
                f"/competitions/{league.api_code}/matches?season={start - 1}&status=FINISHED", ttl=DAY))
        except ApiError as exc:
            sd.notes.append(f"직전 시즌 미제공: {exc}")

        try:
            scorers = self._get(f"/competitions/{league.api_code}/scorers?season={start}&limit=60").get("scorers", [])
            for s in scorers:
                k = key_by_id.get(s["team"]["id"]) or team_key(s["team"]["name"])
                sd.players.append(RawPlayer(
                    k, s["player"]["name"], f"fd:{s['player']['id']}",
                    normalize_position(s["player"].get("section") or s["player"].get("position") or ""),
                    goals=s.get("goals") or 0, assists=s.get("assists") or 0, played=s.get("playedMatches") or 0))
        except ApiError as exc:
            sd.notes.append(f"득점자 미제공: {exc}")
        return sd


# ---------------------------------------------------------------- Fantasy Premier League (부상 정보만)
FPL_BOOTSTRAP_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
_FPL_POSITIONS = {1: "Goalkeeper", 2: "Defence", 3: "Midfield", 4: "Offence"}


class FplInjurySource(Source):
    key = "fpl"
    name = "Fantasy Premier League"
    url = "https://fantasy.premierleague.com"
    license = "비공식 공개 API (공식 이용약관 없음) — 부상·결장 정보에만 사용"
    attribution = "Injury news: Fantasy Premier League"

    def fetch(self, league: League, as_of: date) -> SourceData:
        if league.key != "EPL":
            raise ApiError("프리미어리그만 제공")
        data = cached_get_json(FPL_BOOTSTRAP_URL, ttl=self.ttl)
        sd = SourceData(self.name)
        team_by_id = {}
        for t in data.get("teams", []):
            k = team_key(t["name"])
            team_by_id[t["id"]] = k
            sd.teams.append(RawTeam(k, t["name"], t.get("short_name") or ""))

        for e in data.get("elements", []):
            status = e.get("status", "a")
            if status in ("u", "n"):  # 이적·임대로 팀을 떠난 선수
                continue
            chance = e.get("chance_of_playing_next_round")
            if status == "a":
                prob = 1.0
            elif chance is not None:
                prob = chance / 100
            else:
                prob = 0.5 if status == "d" else 0.0
            team = team_by_id.get(e.get("team"))
            if not team:
                continue
            full = f"{e.get('first_name', '')} {e.get('second_name', '')}".strip()
            sd.players.append(RawPlayer(
                team, e.get("known_name") or full, f"fpl:{e.get('id')}",
                _FPL_POSITIONS.get(e.get("element_type"), ""),
                goals=e.get("goals_scored") or 0, assists=e.get("assists") or 0,
                played=e.get("starts") or 0, minutes=e.get("minutes") or 0,
                play_prob=prob, reason=e.get("news") or ("" if prob == 1 else status),
                aliases=[n for n in (full, e.get("web_name")) if n]))
        return sd


# ---------------------------------------------------------------- API-Football
_AF_FINISHED = {"FT", "AET", "PEN", "AWD", "WO"}
_AF_LIVE = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE", "INT", "SUSP"}
_AF_POSTPONED = {"PST", "CANC", "ABD"}


class ApiFootballSource(Source):
    key = "api-football"
    name = "API-Football"
    url = "https://www.api-football.com"
    license = "무료 플랜 이용약관 (키 필요, 하루 100회)"
    attribution = "Data provided by API-Football (api-sports.io)"
    needs_key = True

    def __init__(self, api_key: str = API_FOOTBALL_KEY, ttl: int = CACHE_TTL_SECONDS):
        super().__init__(ttl)
        self.api_key = api_key

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, ttl: int | None = None) -> list:
        data = cached_get_json(f"{API_FOOTBALL_BASE_URL}{path}", {"x-apisports-key": self.api_key}, ttl or self.ttl)
        # API-Football 은 플랜 제한·키 오류를 HTTP 200 + errors 필드로 알려준다
        errors = data.get("errors") if isinstance(data, dict) else None
        if errors:
            if isinstance(errors, dict) and "plan" in errors:
                raise ApiError(f"요금제 제한: {errors['plan']}")
            raise ApiError(f"{path.split('?')[0]} -> {errors}")
        return data.get("response", []) if isinstance(data, dict) else []

    @staticmethod
    def _matches(rows: list) -> list[RawMatch]:
        out = []
        for r in rows:
            fx, teams, goals = r.get("fixture") or {}, r.get("teams") or {}, r.get("goals") or {}
            short = ((fx.get("status") or {}).get("short") or "").upper()
            status = ("FINISHED" if short in _AF_FINISHED else "IN_PLAY" if short in _AF_LIVE
                      else "POSTPONED" if short in _AF_POSTPONED else "SCHEDULED")
            done = status == "FINISHED" and goals.get("home") is not None
            rnd = "".join(ch for ch in str((r.get("league") or {}).get("round", "")).split("-")[-1] if ch.isdigit())
            if not fx.get("date") or not (teams.get("home") or {}).get("name"):
                continue
            out.append(RawMatch(
                datetime.fromisoformat(fx["date"]).astimezone(timezone.utc),
                team_key(teams["home"]["name"]), team_key(teams["away"]["name"]),
                status if done or status != "FINISHED" else "SCHEDULED",
                goals.get("home") if done else None, goals.get("away") if done else None,
                int(rnd) if rnd else None))
        return out

    @staticmethod
    def _stat_players(rows: list) -> list[RawPlayer]:
        out = []
        for r in rows:
            p, stats = r.get("player") or {}, (r.get("statistics") or [{}])[0]
            team = (stats.get("team") or {}).get("name")
            if not team:
                continue
            games, g = stats.get("games") or {}, stats.get("goals") or {}
            out.append(RawPlayer(
                team_key(team), p.get("name", ""), f"af:{p.get('id')}", normalize_position(games.get("position", "")),
                goals=g.get("total") or 0, assists=g.get("assists") or 0,
                played=games.get("lineups") or games.get("appearences") or 0, minutes=games.get("minutes") or 0))
        return out

    def fetch(self, league: League, as_of: date) -> SourceData:
        sd = SourceData(self.name)
        start = season_start_year(as_of)
        lid = league.api_football_id
        try:
            sd.matches = self._matches(self._get(f"/fixtures?league={lid}&season={start}"))
        except ApiError as exc:
            # 무료 플랜은 지난 시즌만 열려 있어 이번 시즌 일정·부상을 쓸 수 없다 → 출처 전체를 '사용 불가'로
            raise ApiError(f"{start}-{str(start + 1)[2:]} 시즌 사용 불가 — {exc}") from exc
        seen = {}
        for m in sd.matches:
            seen.setdefault(m.home, m.home)
            seen.setdefault(m.away, m.away)
        sd.teams = [RawTeam(k, k) for k in seen]

        try:
            sd.prev_matches = self._matches(self._get(f"/fixtures?league={lid}&season={start - 1}&status=FT", ttl=DAY))
        except ApiError as exc:
            sd.notes.append(f"직전 시즌 미제공: {exc}")

        for path, label in ((f"/players/topscorers?league={lid}&season={start}", "득점"),
                            (f"/players/topassists?league={lid}&season={start}", "도움")):
            try:
                sd.players += self._stat_players(self._get(path))
            except ApiError as exc:
                sd.notes.append(f"{label} 순위 미제공: {exc}")

        # 부상 정보는 다가오는 경기에만 의미가 있고, 날짜마다 요청하면 하루 100회 한도를 넘기므로 가까운 날짜만 조회
        if not -1 <= (as_of - date.today()).days <= 10:
            return sd
        try:
            injuries = self._get(f"/injuries?league={lid}&season={start}&date={as_of.isoformat()}")
            for r in injuries:
                p, t = r.get("player") or {}, r.get("team") or {}
                kind = (p.get("type") or "").lower()
                prob = 0.5 if "question" in kind or "doubt" in kind else 0.0
                sd.players.append(RawPlayer(team_key(t.get("name", "")), p.get("name", ""), f"af:{p.get('id')}",
                                            play_prob=prob, reason=p.get("reason") or p.get("type") or "injury"))
        except ApiError as exc:
            sd.notes.append(f"부상 정보 미제공: {exc}")
        return sd
