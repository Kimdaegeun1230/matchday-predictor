"""여러 출처를 합쳐 하나의 팀/경기/선수 데이터로 만드는 공급자.

판단 규칙
- 같은 경기: 같은 시즌의 (홈, 원정) 조합은 한 번뿐이므로 이를 키로 묶는다 (연기돼 날짜가 달라도 같은 경기).
- 경기 시각·라운드: football-data → API-Football → openfootball 순으로 우선.
- 결과: 한 출처라도 종료 스코어가 있으면 종료로 본다. 출처끼리 스코어가 다르면 우선순위가 높은 출처를
  채택하고 '불일치'로 기록한다. 2개 이상 출처가 같은 스코어면 '교차 확인'.
- 선수: 출처마다 이름 표기가 달라(예: "Bukayo Saka" / "B. Saka") 이름 첫 글자 + 성으로 묶는다.
  득점·도움은 출처 중 큰 값, 출전 확률은 가장 보수적인(낮은) 값, 포지션은 처음 확인된 값.
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata
import zlib
from datetime import date

from ..config import League
from ..models import Match, Player, Team
from .base import DataProvider
from .http import ApiError
from .sources import (ApiFootballSource, FootballDataSource, OpenFootballSource, RawMatch, Source, SourceData,
                      season_start_year)
from .teamnames import display

log = logging.getLogger(__name__)


def _stable_id(text: str) -> int:
    return zlib.crc32(text.encode()) & 0x7FFFFFFF


def player_key(name: str) -> tuple[str, str]:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    tokens = [t for t in re.split(r"[^a-z]+", s) if t]
    if not tokens:
        return ("", "")
    return (tokens[0][0], tokens[-1]) if len(tokens) > 1 else ("", tokens[0])


class MultiSourceProvider(DataProvider):
    name = "multi-source"

    def __init__(self, sources: list[Source] | None = None, memo_seconds: int = 300):
        # 리스트 순서 = 우선순위
        self.sources = sources if sources is not None else [FootballDataSource(), ApiFootballSource(), OpenFootballSource()]
        self.memo_seconds = memo_seconds
        self._memo: dict[tuple, tuple[float, dict]] = {}
        self._as_of = date.today()

    @property
    def active_names(self) -> list[str]:
        return [s.name for s in self.sources if s.enabled]

    # ---------------------------------------------------------------- 수집
    def _collect(self, league: League, as_of: date) -> dict:
        key = (league.key, as_of)
        hit = self._memo.get(key)
        if hit and time.time() - hit[0] < self.memo_seconds:
            return hit[1]

        bundles: list[tuple[Source, SourceData]] = []
        status = []
        for src in self.sources:
            row = {"key": src.key, "name": src.name, "url": src.url, "license": src.license,
                   "attribution": src.attribution, "enabled": src.enabled, "ok": False, "error": "", "notes": [],
                   "counts": {}}
            if not src.enabled:
                row["error"] = "API 키 없음 (.env 에 설정하면 사용)"
            else:
                try:
                    sd = src.fetch(league, as_of)
                    bundles.append((src, sd))
                    row.update(ok=True, notes=sd.notes, counts={
                        "teams": len(sd.teams), "matches": len(sd.matches),
                        "results": sum(m.status == "FINISHED" for m in sd.matches),
                        "previous_season": len(sd.prev_matches), "players": len(sd.players),
                        "injuries": sum(p.play_prob < 1 for p in sd.players)})
                except (ApiError, KeyError, ValueError, TypeError) as exc:
                    log.warning("%s 수집 실패: %s", src.name, exc)
                    row["error"] = str(exc)
            status.append(row)
        if not bundles:
            raise ApiError("사용 가능한 데이터 출처가 없습니다: " + "; ".join(f"{s['name']}: {s['error']}" for s in status))

        merged = self._merge(league, as_of, bundles)
        merged["status"] = status
        self._memo[key] = (time.time(), merged)
        return merged

    # ---------------------------------------------------------------- 병합
    def _merge(self, league: League, as_of: date, bundles: list[tuple[Source, SourceData]]) -> dict:
        start = season_start_year(as_of)
        current_keys: set[str] = set()
        for _, sd in bundles:
            for m in sd.matches:
                current_keys.update((m.home, m.away))

        # 팀 표시 이름: 알려진 이름표 → football-data 짧은 이름 → 키
        hints = {}
        for _, sd in bundles:
            for t in sd.teams:
                hints.setdefault(t.key, (t.name, t.tla))
        teams: dict[str, Team] = {}

        def team(k: str) -> Team:
            if k not in teams:
                name, tla = display(k, *hints.get(k, ("", "")))
                teams[k] = Team(_stable_id(k), name, name, tla, current=k in current_keys)
            return teams[k]

        quality = {"matches": 0, "results": 0, "confirmed": 0, "single_source": 0, "conflicts": [],
                   "missing_in": {}}
        matches: list[Match] = []
        for season, attr in ((start, "matches"), (start - 1, "prev_matches")):
            groups: dict[tuple[str, str], list[tuple[str, RawMatch]]] = {}
            for src, sd in bundles:
                for m in getattr(sd, attr):
                    groups.setdefault((m.home, m.away), []).append((src.name, m))
            for (h, a), rows in groups.items():
                matches.append(self._resolve(season, h, a, rows, team, quality if season == start else None))
            if season == start:
                for src, sd in bundles:
                    missing = len(groups) - len({(m.home, m.away) for m in getattr(sd, attr)})
                    if missing:
                        quality["missing_in"][src.name] = missing

        for t in teams.values():
            t.promoted = t.current and not any(
                m.utc_date.date() < date(start, 7, 1) and t.id in (m.home.id, m.away.id) for m in matches)

        return {"teams": {t.id: t for t in teams.values()}, "matches": matches, "quality": quality,
                "players": self._merge_players(bundles, team)}

    @staticmethod
    def _resolve(season: int, h: str, a: str, rows: list[tuple[str, RawMatch]], team, quality: dict | None) -> Match:
        first = rows[0][1]  # 우선순위 가장 높은 출처
        results = [(name, (m.home_goals, m.away_goals)) for name, m in rows if m.status == "FINISHED"]
        sources = [name for name, _ in rows]
        if results:
            chosen = results[0][1]
            status = "FINISHED"
        else:
            chosen = (None, None)
            status = "IN_PLAY" if any(m.status == "IN_PLAY" for _, m in rows) else first.status

        confirmed = [name for name, sc in results if sc == chosen]
        if quality is not None:
            quality["matches"] += 1
            if results:
                quality["results"] += 1
                if len(confirmed) >= 2:
                    quality["confirmed"] += 1
                else:
                    quality["single_source"] += 1
                if any(sc != chosen for _, sc in results):
                    quality["conflicts"].append({
                        "home": team(h).name, "away": team(a).name, "date": first.utc_date.date().isoformat(),
                        "scores": {name: f"{sc[0]}-{sc[1]}" for name, sc in results},
                        "chosen": f"{chosen[0]}-{chosen[1]}"})

        return Match(
            id=_stable_id(f"{season}|{h}|{a}"),
            utc_date=first.utc_date,
            home=team(h), away=team(a),
            status=status,
            matchday=next((m.matchday for _, m in rows if m.matchday), None),
            home_goals=chosen[0], away_goals=chosen[1],
            sources=sources, confirmed_by=confirmed,
        )

    @staticmethod
    def _merge_players(bundles, team) -> dict[int, list[Player]]:
        merged: dict[tuple[str, tuple[str, str]], Player] = {}
        by_source_id: dict[str, Player] = {}
        for src, sd in bundles:
            for rp in sd.players:
                if not rp.team or not rp.name:
                    continue
                t = team(rp.team)
                pk = player_key(rp.name)
                p = by_source_id.get(rp.source_id) if rp.source_id else None
                p = p or merged.get((rp.team, pk))
                if p is None:
                    p = Player(id=_stable_id(f"{rp.team}|{pk}"), name=rp.name, team_id=t.id)
                    merged[(rp.team, pk)] = p
                if rp.source_id:
                    by_source_id[rp.source_id] = p
                # 더 긴(정식) 이름을 표시용으로
                if len(rp.name) > len(p.name) and "." not in rp.name:
                    p.name = rp.name
                p.position = p.position or rp.position
                p.goals = max(p.goals, rp.goals)
                p.assists = max(p.assists, rp.assists)
                p.played = max(p.played, rp.played)
                p.minutes = max(p.minutes, rp.minutes)
                if rp.play_prob < p.play_prob:
                    p.play_prob, p.absence_reason = rp.play_prob, rp.reason
        out: dict[int, list[Player]] = {}
        for p in merged.values():
            out.setdefault(p.team_id, []).append(p)
        return out

    # ---------------------------------------------------------------- DataProvider
    def get_matches(self, league: League, as_of: date) -> list[Match]:
        self._as_of = as_of
        return self._collect(league, as_of)["matches"]

    def get_teams(self, league: League) -> dict[int, Team]:
        return self._collect(league, self._as_of)["teams"]

    def get_players(self, league: League, teams: dict[int, Team]) -> dict[int, list[Player]]:
        return self._collect(league, self._as_of)["players"]

    def report(self, league: League, as_of: date) -> dict:
        data = self._collect(league, as_of)
        return {"sources": data["status"], "reconciliation": data["quality"]}
