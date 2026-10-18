"""매체 예측 기록 저장소와 칼럼니스트별 적중률.

파일 형식 (data/media/media.json)
{
  "predictions": [{fixture_id, home, away, kickoff, outlet, author, home_goals, away_goals, url, headline, collected_at}],
  "collected":   {"<fixture_id>": "<마지막 수집 시각>"}
}

적중률은 끝난 경기 결과와 비교해 칼럼니스트(매체 + 작성자)마다 계산한다.
기록이 적으면 사전값(50%) 쪽으로 당겨서, 한두 번 맞힌 사람이 과하게 믿어지지 않게 한다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import MEDIA, MEDIA_FILE, MediaConfig
from ..models import Match


def outcome(h: int, a: int) -> str:
    return "H" if h > a else "D" if h == a else "A"


def pundit_key(p: dict) -> str:
    return f"{p['outlet'].strip().lower()}|{p['author'].strip().lower()}"


@dataclass
class PunditStats:
    outlet: str
    author: str
    games: int = 0
    outcome_hits: int = 0
    exact_hits: int = 0

    def hit_rate(self, cfg: MediaConfig = MEDIA) -> float:
        """사전값으로 보정한 결과(승/무/패) 적중률."""
        return (self.outcome_hits + cfg.prior_hit_rate * cfg.prior_games) / (self.games + cfg.prior_games)

    def weight(self, cfg: MediaConfig = MEDIA) -> float:
        """반영 가중치. 적중률 50% = 1.0, 적중률이 높을수록 크게 (제곱으로 차이를 키움)."""
        return (self.hit_rate(cfg) / cfg.prior_hit_rate) ** 2

    def to_dict(self, cfg: MediaConfig = MEDIA) -> dict:
        raw = self.outcome_hits / self.games if self.games else None
        return {"outlet": self.outlet, "author": self.author, "games": self.games,
                "outcome_hits": self.outcome_hits, "exact_hits": self.exact_hits,
                "raw_hit_rate": round(raw, 3) if raw is not None else None,
                "hit_rate": round(self.hit_rate(cfg), 3), "weight": round(self.weight(cfg), 3)}


class MediaStore:
    def __init__(self, path: Path = MEDIA_FILE, config: MediaConfig = MEDIA):
        self.path = path
        self.cfg = config
        self.predictions: list[dict] = []
        self.collected: dict[str, str] = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.predictions = data.get("predictions", [])
            self.collected = data.get("collected", {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"predictions": self.predictions, "collected": self.collected},
                                        ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------------------------------------------------------------- 수집 대상 고르기
    def due_fixtures(self, matches: list[Match], now: datetime | None = None) -> list[Match]:
        """수집할 경기: 72시간 안에 시작하는 경기(아직 안 모았거나, 킥오프 하루 전 다시) +
        적중률 계산용으로 최근 3주 안에 끝났는데 아직 안 모은 경기."""
        now = now or datetime.now(timezone.utc)
        cfg = self.cfg
        due = []
        for m in matches:
            if not m.home.current:
                continue
            last = self.collected.get(str(m.id))
            last_dt = datetime.fromisoformat(last) if last else None
            until = m.utc_date - now
            if not m.finished and timedelta(0) < until <= timedelta(hours=cfg.lookahead_hours):
                stale = last_dt is None or (now - last_dt > timedelta(hours=cfg.refresh_hours)
                                            and until <= timedelta(hours=cfg.refresh_hours + 6))
                if stale:
                    due.append(m)
            elif m.finished and last_dt is None and timedelta(0) < now - m.utc_date <= timedelta(days=cfg.backfill_days):
                due.append(m)
        return due

    @staticmethod
    def group_by_day(matches: list[Match]) -> list[list[Match]]:
        """같은 날짜 경기를 한 번의 Claude 호출로 묶는다. 다가오는 경기 먼저, 그다음 최근에 끝난 경기."""
        days: dict = {}
        for m in matches:
            days.setdefault(m.utc_date.date(), []).append(m)
        upcoming = sorted((d for d, ms in days.items() if not ms[0].finished))
        past = sorted((d for d, ms in days.items() if ms[0].finished), reverse=True)
        return [days[d] for d in upcoming + past]

    def add(self, fixture_ids: list[str], predictions: list[dict], when: datetime | None = None) -> int:
        when_s = (when or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        for fid in fixture_ids:
            self.collected[fid] = when_s
        index = {(p["fixture_id"], pundit_key(p)): i for i, p in enumerate(self.predictions)}
        added = 0
        for p in predictions:
            k = (p["fixture_id"], pundit_key(p))
            if k in index:
                self.predictions[index[k]] = p   # 같은 칼럼니스트의 최신 예측으로 교체
            else:
                index[k] = len(self.predictions)
                self.predictions.append(p)
                added += 1
        return added

    # ---------------------------------------------------------------- 적중률
    def pundit_stats(self, results: dict[str, tuple[int, int]]) -> dict[str, PunditStats]:
        """results: {fixture_id: (홈골, 원정골)} — 끝난 경기."""
        stats: dict[str, PunditStats] = {}
        for p in self.predictions:
            s = stats.setdefault(pundit_key(p), PunditStats(p["outlet"], p["author"]))
            res = results.get(p["fixture_id"])
            if res is None:
                continue
            s.games += 1
            s.outcome_hits += outcome(p["home_goals"], p["away_goals"]) == outcome(*res)
            s.exact_hits += (p["home_goals"], p["away_goals"]) == tuple(res)
        return stats

    def for_fixture(self, fixture_id: str) -> list[dict]:
        return [p for p in self.predictions if p["fixture_id"] == fixture_id]
