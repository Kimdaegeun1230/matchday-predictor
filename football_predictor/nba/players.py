"""선수별 득점 예측: 지난 시즌 경기별 득점 분포 + 이번 경기 팀 예상 득점.

    지난 시즌 경기별 득점(ESPN) ─▶ 팀 예상 득점에 맞춰 비율 조정 ─▶ 예상 득점·N점 이상 확률

- 지난 시즌 기록은 바뀌지 않으므로 `python main.py nba-players` 로 한 번 받아 data/nba_players.json 에 둔다.
- 파일에는 날짜·상대 없이 정렬한 득점 목록만 저장한다 (분포만 있으면 됨).
- 소속팀은 ESPN 현재 로스터로 맞춘다 (이적 반영). 로스터를 못 받으면 파일에 적힌 팀을 쓴다.
"""
from __future__ import annotations

import json
import logging
import math
import statistics
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..config import DATA_DIR, ESPN_NBA_BASE_URL, NBA, NbaConfig
from ..providers.http import ApiError, cached_get_json
from .sources import team_key

log = logging.getLogger("agent.nba")
PLAYERS_FILE = DATA_DIR / "nba_players.json"
GAMELOG_URL = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/{id}/gamelog?season={season}"
THRESHOLDS = (10, 15, 20, 25, 30, 40)
DAY = 24 * 3600
YEAR = 365 * DAY


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


class EspnRosterSource:
    """ESPN 현재 로스터 (비공식 API)."""

    def __init__(self, ttl: int = DAY):
        self.ttl = ttl

    def teams(self) -> list[dict]:
        data = cached_get_json(f"{ESPN_NBA_BASE_URL}/teams", ttl=self.ttl)
        return [t["team"] for t in data["sports"][0]["leagues"][0]["teams"]]

    def rosters(self) -> dict[str, list[dict]]:
        out = {}
        for t in self.teams():
            data = cached_get_json(f"{ESPN_NBA_BASE_URL}/teams/{t['id']}/roster", ttl=self.ttl)
            out[team_key(t["displayName"])] = [{"id": str(a["id"]), "name": a["displayName"]}
                                               for a in data.get("athletes", [])]
        return out


def parse_gamelog(data: dict) -> tuple[list[int], float]:
    """정규시즌 경기별 득점과 평균 출전 시간. 플레이오프·프리시즌은 뺀다."""
    names = data.get("names") or []
    if "points" not in names or "minutes" not in names:
        return [], 0.0
    ip, im = names.index("points"), names.index("minutes")
    seen, pts, mins = set(), [], []
    for st in data.get("seasonTypes", []):
        if "Regular Season" not in str(st.get("displayName")):
            continue
        for cat in st.get("categories", []):
            for ev in cat.get("events", []):
                if ev.get("eventId") in seen:
                    continue
                seen.add(ev.get("eventId"))
                row = ev.get("stats") or []
                try:
                    pts.append(int(float(row[ip])))
                    mins.append(float(row[im]))
                except (IndexError, ValueError):
                    continue
    return pts, (sum(mins) / len(mins) if mins else 0.0)


def fetch_players(season_end: int, source: EspnRosterSource | None = None, interval: float = 0.2) -> dict:
    """현재 로스터 선수들의 지난 시즌 기록을 내려받는다. season_end: 2026 이면 2025-26 시즌."""
    rosters = (source or EspnRosterSource()).rosters()
    players, failed = {}, 0
    for team, roster in rosters.items():
        for a in roster:
            try:
                data = cached_get_json(GAMELOG_URL.format(id=a["id"], season=season_end), ttl=YEAR)
            except ApiError as exc:
                log.info("기록 없음 %s: %s", a["name"], exc)
                failed += 1
                continue
            pts, mpg = parse_gamelog(data)
            if pts:
                players[a["id"]] = {"name": a["name"], "team": team, "gp": len(pts), "mpg": round(mpg, 1),
                                    "pts": sorted(pts)}
            time.sleep(interval)
    return {"season": f"{season_end - 1}-{str(season_end)[2:]}", "season_end": season_end,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "failed": failed, "players": players, "backtest": {}}


def load_players(path: Path = PLAYERS_FILE) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def team_lines(players: list[dict], team_points: float, config: NbaConfig = NBA) -> list[dict]:
    """한 팀 선수들의 예상 득점. players: {name, pts(경기별), gp, mpg, play_prob}.

    출전 예상 상위 선수(로테이션)의 평균 득점 합을 팀 예상 득점에 맞춘다: 주전이 빠지면 나머지 몫이 늘고
    약한 수비 상대로 다 같이 오른다. 로스터 전원을 더하면 안 뛰는 선수까지 들어가 모두 낮게 나온다.
    """
    pool = [p for p in players if p["gp"] >= config.player_min_games]
    denom = sum(sorted((p["play_prob"] * sum(p["pts"]) / p["gp"] for p in pool), reverse=True)[:config.player_rotation])
    if not denom:
        return []
    ratio = max(config.player_ratio_min, min(config.player_ratio_max, team_points / denom))
    out = []
    for p in pool:
        scaled = sorted(x * ratio for x in p["pts"])
        ppg = sum(p["pts"]) / p["gp"]
        q = statistics.quantiles(scaled, n=10, method="inclusive")
        row = {"name": p["name"], "ppg": round(ppg, 1), "gp": p["gp"], "mpg": p["mpg"], "play_prob": p["play_prob"],
               "expected": round(ppg * ratio, 1), "low": round(q[0]), "high": round(q[-1])}
        for k in THRESHOLDS:
            hit = sum(_phi((x - k + 0.5) / config.player_bandwidth) for x in scaled) / len(scaled)
            row[f"p{k}"] = round(p["play_prob"] * hit, 4)
        out.append(row)
    out.sort(key=lambda r: -r["expected"] * r["play_prob"])
    return out


def box_scores(start: date, end: date) -> list[dict]:
    """ESPN 끝난 경기 박스스코어: [{date, home, away, teams: {팀: {선수id: 득점}}}] (실제로 뛴 선수만)."""
    out, d = [], start
    while d <= end:
        board = cached_get_json(f"{ESPN_NBA_BASE_URL}/scoreboard?dates={d:%Y%m%d}", ttl=DAY)
        for ev in board.get("events", []):
            if not ev.get("status", {}).get("type", {}).get("completed"):
                continue
            comp = ev["competitions"][0]["competitors"]
            side = {c["homeAway"]: team_key(c["team"]["displayName"]) for c in comp}
            box = cached_get_json(f"{ESPN_NBA_BASE_URL}/summary?event={ev['id']}", ttl=YEAR)
            teams = {}
            for t in box.get("boxscore", {}).get("players", []):
                st = t["statistics"][0]
                ip, im = st["names"].index("PTS"), st["names"].index("MIN")
                teams[team_key(t["team"]["displayName"])] = {
                    str(a["athlete"]["id"]): int(a["stats"][ip]) for a in st["athletes"]
                    if a.get("stats") and not a.get("didNotPlay") and a["stats"][im] not in ("0", "--")}
            out.append({"date": d, "home": side["home"], "away": side["away"], "teams": teams})
        d += timedelta(days=1)
    return out


def score_backtest(games: list[dict], players: dict, config: NbaConfig = NBA) -> dict:
    """games: [{team_points, actual: {선수id: 득점}, roster?: [선수id]}].

    roster 가 없으면 실제 출전 명단을 안다고 가정, 있으면 부상 정보 없이 로스터 전원을 예측하고 실제로 뛴 선수만 채점한다.
    """
    n = err = err_ppg = top_hit = top_n = 0
    probs = {k: [] for k in THRESHOLDS}
    for g in games:
        known = [{**players[i], "id": i, "play_prob": 1.0} for i in g.get("roster", g["actual"]) if i in players]
        rows = team_lines(known, g["team_points"], config)
        if not rows:
            continue
        ids = {p["name"]: p["id"] for p in known}
        for r in rows:
            if ids[r["name"]] not in g["actual"]:
                continue
            actual = g["actual"][ids[r["name"]]]
            n += 1
            err += abs(r["expected"] - actual)
            err_ppg += abs(r["ppg"] - actual)
            for k in THRESHOLDS:
                probs[k].append((r[f"p{k}"], actual >= k))
        top_n += 1
        top_hit += g["actual"].get(ids[rows[0]["name"]]) == max(g["actual"].values())
    res = {"players": n, "team_games": top_n, "mae": round(err / n, 2) if n else None,
           "mae_ppg": round(err_ppg / n, 2) if n else None,
           "top_scorer_rate": round(top_hit / top_n, 3) if top_n else None, "thresholds": {}}
    for k, rows in probs.items():
        if rows:
            rate = sum(h for _, h in rows) / len(rows)
            res["thresholds"][str(k)] = {
                "predicted": round(sum(p for p, _ in rows) / len(rows), 3), "actual": round(rate, 3),
                "brier": round(sum((p - h) ** 2 for p, h in rows) / len(rows), 4),
                "brier_base": round(sum((rate - h) ** 2 for _, h in rows) / len(rows), 4)}
    # 확률 구간별 실제 비율 (20점 이상)
    bins = {}
    for p, h in probs[20]:
        c = bins.setdefault(min(int(p * 5), 4), [0, 0, 0.0])
        c[0] += 1
        c[1] += h
        c[2] += p
    res["calibration_20"] = [{"range": f"{b * 20}-{(b + 1) * 20}%", "n": c[0], "predicted": round(c[2] / c[0], 3),
                              "actual": round(c[1] / c[0], 3)} for b, c in sorted(bins.items())]
    return res
