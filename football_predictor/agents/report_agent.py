"""ReportAgent: 예측 결과를 웹/외부에서 읽을 수 있는 JSON으로 발행한다."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ..config import MODEL, OUTPUT_DIR
from .base import Agent, MatchContext


def _teams_table(ctx: MatchContext) -> list[dict]:
    rows = []
    for row in ctx.standings:
        tid = row["team_id"]
        team = ctx.teams[tid]
        s = ctx.team_strengths.get(tid)
        if s is None:
            continue
        rows.append({
            **row,
            "name": team.name,
            "tla": team.tla or team.name[:3].upper(),
            "promoted": team.promoted,
            "attack_home": round(s.attack_home, 3),
            "attack_away": round(s.attack_away, 3),
            "defense_home": round(s.defense_home, 3),
            "defense_away": round(s.defense_away, 3),
            "rating": round(s.rating, 3),
            "form": s.form,
            "form_ppg": round(s.form_ppg, 2),
        })
    return rows


def build_report(ctx: MatchContext) -> dict:
    matchdays = sorted({m.matchday for m in ctx.fixtures if m.matchday})
    return {
        "league": ctx.league.key,
        "league_name": ctx.league.name,
        "date": ctx.target_date.isoformat(),
        "matchdays": matchdays,
        "data_source": ctx.data_source,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "league_avg": {"home": round(ctx.league_avg_home_goals, 3), "away": round(ctx.league_avg_away_goals, 3)},
        "training_matches": len(ctx.finished_matches),
        "predictions": [p.to_dict() for p in sorted(ctx.predictions, key=lambda p: p.kickoff)],
        "teams": _teams_table(ctx),
        "model": asdict(MODEL),
        "sources": ctx.source_report.get("sources", []),
        "reconciliation": {k: v for k, v in ctx.source_report.get("reconciliation", {}).items() if k != "conflicts"},
        "data_quality": ctx.data_quality,
        "agent_log": ctx.log,
    }


class ReportAgent(Agent):
    name = "reporter"
    role = "예측 리포트 발행(JSON)"

    def __init__(self, output_dir: Path = OUTPUT_DIR, write_file: bool = True):
        super().__init__()
        self.output_dir = output_dir
        self.write_file = write_file
        self.last_path: Path | None = None

    def run(self, ctx: MatchContext) -> str:
        if not self.write_file:
            return "파일 저장 생략"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / f"{ctx.league.key}_{ctx.target_date.isoformat()}.json"
        # 로그에 자기 자신이 들어가기 전에 쓰므로 reporter 항목은 파일에 포함되지 않음
        path.write_text(json.dumps(build_report(ctx), ensure_ascii=False, indent=2), encoding="utf-8")
        self.last_path = path
        return f"저장: {path.name}"
