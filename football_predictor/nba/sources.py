"""NBA 데이터 출처.

| 출처                 | 약관                         | 가져오는 것                              | 키 |
|----------------------|------------------------------|------------------------------------------|----|
| balldontlie          | 이용약관 有 (공개 표시·상업 이용 허용, 출처 표기 의무 없음) | 팀, 이번·직전 시즌 경기 일정·결과 | 무료 키 (분당 5회) |
| ESPN (비공식)        | 공식 이용약관 없음           | 부상·결장, 결장 선수의 평균 득점·출전 시간 | 불필요 |

ESPN 은 공개 문서·약관이 없는 비공식 API 라, 부상 보강에만 쓰고 사이트에 '비공식'으로 표시한다.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from ..config import (BALLDONTLIE_API_KEY, BALLDONTLIE_BASE_URL, CACHE_TTL_SECONDS, ESPN_ATHLETE_URL,
                      ESPN_NBA_BASE_URL, NBA, NbaConfig)
from ..providers.http import ApiError, cache_fresh, cached_get_json

log = logging.getLogger(__name__)
DAY = 24 * 3600


def team_key(name: str) -> str:
    """팀 별칭(마지막 단어)이 NBA 30팀에서 모두 달라 공통 키로 쓴다: 'Portland Trail Blazers' → 'blazers'."""
    return name.strip().split()[-1].lower()


def season_of(d: date) -> int:
    """NBA 시즌 표기: 2026-27 시즌 = 2026 (10월 개막)."""
    return d.year if d.month >= 9 else d.year - 1


@dataclass
class NbaTeam:
    key: str
    name: str
    abbr: str
    conference: str = ""
    division: str = ""


@dataclass
class NbaGame:
    id: int
    date: date                  # 미국 현지 경기일
    tipoff: datetime | None     # UTC
    home: str                   # team_key
    away: str
    status: str                 # FINISHED / SCHEDULED / IN_PLAY
    home_pts: int | None = None
    away_pts: int | None = None
    postseason: bool = False
    season: int = 0

    @property
    def finished(self) -> bool:
        return self.status == "FINISHED" and self.home_pts is not None


@dataclass
class NbaInjury:
    team: str                   # team_key
    player: str
    status: str                 # Out / Day-To-Day / Questionable / Doubtful / Suspension …
    play_prob: float
    detail: str = ""
    ppg: float = 0.0
    mpg: float = 0.0
    games_played: int = 0
    espn_id: str = ""


_PLAY_PROB = {"out": 0.0, "suspension": 0.0, "doubtful": 0.25, "questionable": 0.5,
              "day-to-day": 0.5, "probable": 0.85}


class BalldontlieSource:
    key = "balldontlie"
    name = "balldontlie"
    url = "https://www.balldontlie.io"
    license = "이용약관 (공개 표시·상업 이용 허용, 무료 키·분당 5회)"
    attribution = "NBA data: balldontlie"

    def __init__(self, api_key: str = BALLDONTLIE_API_KEY, config: NbaConfig = NBA, ttl: int = CACHE_TTL_SECONDS):
        self.api_key = api_key
        self.cfg = config
        self.ttl = ttl
        self._last_request = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, ttl: int) -> dict:
        url = f"{BALLDONTLIE_BASE_URL}{path}"
        if not cache_fresh(url, ttl):  # 무료 등급 분당 5회 → 실제 요청 사이 간격을 둔다
            wait = self.cfg.request_interval - (time.time() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.time()
        return cached_get_json(url, {"Authorization": self.api_key}, ttl)

    @staticmethod
    def _team(raw: dict) -> NbaTeam:
        return NbaTeam(team_key(raw["full_name"]), raw["full_name"], raw.get("abbreviation", ""),
                       raw.get("conference", ""), raw.get("division", ""))

    def teams(self) -> dict[str, NbaTeam]:
        data = self._get("/teams?per_page=100", ttl=30 * DAY)
        # 1946~ 해체 팀도 섞여 있어 동·서부 컨퍼런스가 있는 현재 30팀만
        return {t.key: t for t in (self._team(r) for r in data.get("data", [])) if t.conference in ("East", "West")}

    def season_games(self, season: int, current: bool) -> list[NbaGame]:
        ttl = self.ttl if current else 30 * DAY
        games, cursor = [], None
        for _ in range(40):
            path = f"/games?seasons[]={season}&per_page=100" + (f"&cursor={cursor}" if cursor else "")
            data = self._get(path, ttl)
            for g in data.get("data", []):
                games.append(self._game(g))
            cursor = (data.get("meta") or {}).get("next_cursor")
            if not cursor:
                break
        return games

    @staticmethod
    def _game(g: dict) -> NbaGame:
        status_raw = str(g.get("status") or "")
        hs, vs = g.get("home_team_score"), g.get("visitor_team_score")
        if status_raw.lower().startswith("final"):
            status = "FINISHED"
        elif g.get("period") and (hs or vs):
            status = "IN_PLAY"
        else:
            status = "SCHEDULED"
        tip = g.get("datetime") or (status_raw if "T" in status_raw else None)
        tipoff = datetime.fromisoformat(tip.replace("Z", "+00:00")) if tip else None
        return NbaGame(
            id=int(g["id"]), date=date.fromisoformat(str(g["date"])[:10]), tipoff=tipoff,
            home=team_key(g["home_team"]["full_name"]), away=team_key(g["visitor_team"]["full_name"]),
            status=status, home_pts=hs if status == "FINISHED" else None,
            away_pts=vs if status == "FINISHED" else None,
            postseason=bool(g.get("postseason")), season=int(g.get("season") or 0))


class EspnInjurySource:
    key = "espn"
    name = "ESPN"
    url = "https://www.espn.com/nba/injuries"
    license = "비공식 공개 API (공식 이용약관 없음) — 부상·결장 정보에만 사용"
    attribution = "Injury news: ESPN"
    enabled = True

    def __init__(self, ttl: int = CACHE_TTL_SECONDS):
        self.ttl = ttl

    def injuries(self) -> list[NbaInjury]:
        data = cached_get_json(f"{ESPN_NBA_BASE_URL}/injuries", ttl=self.ttl)
        out = []
        for t in data.get("injuries", []):
            team = team_key(t.get("displayName", ""))
            for i in t.get("injuries", []):
                a = i.get("athlete") or {}
                status = str(i.get("status") or "")
                prob = _PLAY_PROB.get(status.lower(), 0.5)
                espn_id = next((l["href"].split("/id/")[1].split("/")[0] for l in a.get("links", [])
                                if "/id/" in l.get("href", "")), "")
                inj = NbaInjury(team, a.get("displayName", ""), status, prob,
                                str(i.get("shortComment") or "")[:200], espn_id=espn_id)
                if espn_id and prob < 1:
                    self._fill_stats(inj)
                out.append(inj)
        return out

    def _fill_stats(self, inj: NbaInjury) -> None:
        try:
            data = cached_get_json(ESPN_ATHLETE_URL.format(id=inj.espn_id), ttl=12 * 3600)
        except ApiError as exc:
            log.info("선수 기록 없음 %s: %s", inj.player, exc)
            return
        stats = data.get("statistics") or {}
        names = stats.get("names", [])
        split = next((s for s in stats.get("splits", []) if s.get("displayName") == "Regular Season"), None)
        if not split:
            return
        row = dict(zip(names, split.get("stats", [])))

        def num(k):
            try:
                return float(row.get(k, 0) or 0)
            except ValueError:
                return 0.0
        inj.ppg, inj.mpg, inj.games_played = num("avgPoints"), num("avgMinutes"), int(num("gamesPlayed"))


@dataclass
class NbaData:
    teams: dict[str, NbaTeam] = field(default_factory=dict)
    games: list[NbaGame] = field(default_factory=list)
    injuries: list[NbaInjury] = field(default_factory=list)
    status: list[dict] = field(default_factory=list)


def collect(as_of: date, games_source: BalldontlieSource | None = None,
            injury_source: EspnInjurySource | None = None) -> NbaData:
    """두 출처에서 모아 하나로. 출처마다 성공/실패를 status 에 남긴다."""
    gs = games_source or BalldontlieSource()
    inj = injury_source or EspnInjurySource()
    out = NbaData()
    season = season_of(as_of)

    row = {"key": gs.key, "name": gs.name, "url": gs.url, "license": gs.license, "attribution": gs.attribution,
           "enabled": gs.enabled, "ok": False, "error": "", "notes": [], "counts": {}}
    if not gs.enabled:
        row["error"] = "API 키 없음 (.env 에 BALLDONTLIE_API_KEY 설정)"
    else:
        try:
            out.teams = gs.teams()
            cur = gs.season_games(season, current=True)
            try:
                prev = gs.season_games(season - 1, current=False)
            except ApiError as exc:
                prev = []
                row["notes"].append(f"직전 시즌 미제공: {exc}")
            out.games = prev + cur
            row.update(ok=True, counts={"teams": len(out.teams), "matches": len(cur),
                                        "results": sum(g.finished for g in cur), "previous_season": len(prev)})
            if cur and not any(g.finished for g in cur) and as_of > date(season, 10, 25):
                row["notes"].append("이번 시즌 결과가 하나도 없음 — 무료 등급에서 이번 시즌이 막혔는지 확인 필요")
        except ApiError as exc:
            row["error"] = str(exc)
    out.status.append(row)
    if not out.games:
        raise ApiError("NBA 경기 데이터를 가져오지 못했습니다: " + (row["error"] or "결과 없음"))

    row = {"key": inj.key, "name": inj.name, "url": inj.url, "license": inj.license, "attribution": inj.attribution,
           "enabled": True, "ok": False, "error": "", "notes": [], "counts": {}}
    try:
        out.injuries = inj.injuries()
        row.update(ok=True, counts={"injuries": sum(i.play_prob < 1 for i in out.injuries),
                                    "players": len(out.injuries)})
    except (ApiError, KeyError, ValueError) as exc:
        row["error"] = str(exc)
    out.status.append(row)
    return out
