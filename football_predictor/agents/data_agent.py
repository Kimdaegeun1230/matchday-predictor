"""DataCollectorAgent: 리그의 팀·경기·선수 데이터를 수집해 컨텍스트에 적재한다."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from ..config import DATA_DIR
from ..providers.base import DataProvider
from .base import Agent, MatchContext

AVAILABILITY_FILE = DATA_DIR / "availability.json"


class DataCollectorAgent(Agent):
    name = "data_collector"
    role = "팀/경기/선수 데이터 수집"

    def __init__(self, provider: DataProvider):
        super().__init__()
        self.provider = provider

    def run(self, ctx: MatchContext) -> str:
        p = self.provider
        matches = p.get_matches(ctx.league, ctx.target_date)
        ctx.teams = p.get_teams(ctx.league)
        ctx.players = p.get_players(ctx.league, ctx.teams)
        ctx.data_source = p.name
        if hasattr(p, "report"):  # 여러 출처를 합치는 공급자: 출처별 상태와 병합 결과
            ctx.source_report = p.report(ctx.league, ctx.target_date)
            ok = [s["name"] for s in ctx.source_report["sources"] if s["ok"]]
            ctx.data_source = " + ".join(ok)

        cutoff = datetime.combine(ctx.target_date, datetime.min.time(), tzinfo=timezone.utc)
        seen: set[int] = set()
        for m in sorted(matches, key=lambda m: m.utc_date):
            if m.id in seen:
                continue
            seen.add(m.id)
            ctx.all_matches.append(m)
            if m.finished and m.utc_date < cutoff:
                ctx.finished_matches.append(m)
            if m.utc_date.date() == ctx.target_date:
                ctx.fixtures.append(m)
            # 경기에 등장했지만 팀 목록에 없는 팀(직전 시즌 강등팀 등) 보충
            ctx.teams.setdefault(m.home.id, m.home)
            ctx.teams.setdefault(m.away.id, m.away)

        applied = self._apply_availability_overrides(ctx)
        return (f"[{ctx.data_source}] 팀 {len(ctx.teams)}, 완료 경기 {len(ctx.finished_matches)}, "
                f"{ctx.target_date} 경기 {len(ctx.fixtures)}, 수동 결장 반영 {applied}")

    def _apply_availability_overrides(self, ctx: MatchContext) -> int:
        """data/availability.json 의 부상/징계 정보를 선수 데이터에 반영.

        형식: {"EPL": {"Arsenal FC": [{"player": "Bukayo Saka", "reason": "injury"}]}}
        """
        if not AVAILABILITY_FILE.exists():
            return 0
        overrides = json.loads(AVAILABILITY_FILE.read_text(encoding="utf-8")).get(ctx.league.key, {})
        name_to_id = {t.name.lower(): tid for tid, t in ctx.teams.items()}
        name_to_id.update({t.short_name.lower(): tid for tid, t in ctx.teams.items() if t.short_name})
        count = 0
        for team_name, entries in overrides.items():
            tid = name_to_id.get(team_name.lower())
            if tid is None:
                self.logger.warning("결장 정보: 팀을 찾을 수 없음 '%s'", team_name)
                continue
            for entry in entries:
                target = entry["player"].lower()
                for pl in ctx.players.get(tid, []):
                    if pl.name.lower() == target:
                        pl.play_prob = float(entry.get("play_prob", 0.0))
                        pl.absence_reason = entry.get("reason", "unavailable")
                        count += 1
                        break
                else:
                    self.logger.warning("결장 정보: '%s' 선수를 %s 스쿼드에서 찾을 수 없음", entry["player"], team_name)
        return count
