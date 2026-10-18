"""PlayerAnalystAgent: 선수 기여도와 결장 확률을 분석해 팀 공격/수비 보정 계수를 만든다.

- 공격 기여도 = 실제 득점·도움과 기대득점(xG)·기대도움(xA)의 평균 (xG가 있으면 운의 영향을 줄임)
- 결장 영향 = 기여도 × (1 - 출전 확률). 출전 확률 75% 선수는 기여도의 25%만 손실로 본다.
- 수비 영향은 출전 시간 비중으로 가중해, 거의 안 뛰는 백업 수비수/GK 부상은 무시된다.
"""
from __future__ import annotations

from ..config import MODEL, ModelConfig
from ..models import Player, SquadReport
from .base import Agent, MatchContext

_DEFENSIVE_WEIGHT = {"Goalkeeper": 1.5, "Defence": 1.0}
_MAX_LISTED = 5


def attacking_contribution(p: Player, has_xg: bool) -> float:
    actual = p.goals + 0.5 * p.assists
    if not has_xg:
        return actual
    return 0.5 * actual + 0.5 * (p.xg + 0.5 * p.xa)


class PlayerAnalystAgent(Agent):
    name = "player_analyst"
    role = "선수 기여도/결장 영향 분석"

    def __init__(self, config: ModelConfig = MODEL):
        super().__init__()
        self.cfg = config

    def run(self, ctx: MatchContext) -> str:
        involved = {m.home.id for m in ctx.fixtures} | {m.away.id for m in ctx.fixtures}
        total_missing = 0
        no_player_data = []
        for tid in involved:
            squad = ctx.players.get(tid, [])
            report = SquadReport(team_id=tid)
            if not squad:
                no_player_data.append(ctx.teams[tid].name)

            has_xg = any(p.xg or p.xa for p in squad)
            contrib = {p.id: attacking_contribution(p, has_xg) for p in squad}
            total = sum(contrib.values())
            max_minutes = max((p.minutes for p in squad), default=0)

            def regularity(p: Player) -> float:
                return min(1.0, p.minutes / max_minutes) if max_minutes else 1.0

            doubtful = [p for p in squad if p.play_prob < 1.0]
            if total > 0:
                lost_share = sum(contrib[p.id] * (1 - p.play_prob) for p in doubtful) / total
                report.attack_factor = max(0.75, 1.0 - self.cfg.absence_attack_impact * lost_share)

            def_weight = sum(_DEFENSIVE_WEIGHT.get(p.position, 0.0) * (1 - p.play_prob) * regularity(p)
                             for p in doubtful)
            report.defense_factor = min(1.15, 1.0 + self.cfg.absence_defense_impact * def_weight)

            # 영향이 큰 결장/불확실 선수부터 표시 (출전 시간이 거의 없는 선수는 제외)
            relevant = [p for p in doubtful if contrib[p.id] > 0 or regularity(p) >= 0.3]
            relevant.sort(key=lambda p: (contrib[p.id] / total if total else 0) + 0.1 * regularity(p), reverse=True)
            report.missing_players = [
                {"name": p.name, "position": p.position, "play_prob": round(p.play_prob, 2),
                 "reason": p.absence_reason or "out"}
                for p in relevant[:_MAX_LISTED]
            ]

            ranked = sorted((p for p in squad if contrib[p.id] > 0 and p.available),
                            key=lambda p: contrib[p.id], reverse=True)
            report.key_players = [
                {"name": p.name, "position": p.position, "goals": p.goals, "assists": p.assists,
                 "xg": round(p.xg, 2) if has_xg else None}
                for p in ranked[:3]
            ]

            ctx.squad_reports[tid] = report
            total_missing += len(relevant)

        if no_player_data:
            self.logger.warning("선수 데이터 없음 → 선수 보정 없이 팀 전력만 사용: %s", ", ".join(no_player_data))
        summary = f"분석 팀 {len(involved)}, 결장/불확실 주요 선수 {total_missing}명 반영"
        if no_player_data:
            summary += f" (선수 데이터 없는 팀 {len(no_player_data)})"
        return summary
