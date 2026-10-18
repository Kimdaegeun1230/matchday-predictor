"""DataQualityAgent: 여러 출처를 합친 결과를 점검해, 예측을 얼마나 믿을 수 있는지 판단한다.

점검 항목
- 출처 상태: 몇 개 출처가 응답했는지, 키가 없거나 실패한 출처는 무엇인지
- 결과 교차 확인: 2개 이상 출처가 같은 스코어를 준 경기 비율, 출처 간 스코어 불일치
- 결과 반영 지연: 이미 지난 경기인데 어느 출처에도 결과가 없는 경기 수
- 선수 데이터: 오늘 경기 팀 중 선수·부상 정보가 있는 팀 수
"""
from __future__ import annotations

from datetime import datetime, timezone

from .base import Agent, MatchContext

GOOD, WARN, BAD = "good", "warn", "bad"


class DataQualityAgent(Agent):
    name = "data_quality"
    role = "출처 교차 검증/데이터 품질 판단"

    def run(self, ctx: MatchContext) -> str:
        checks = []
        report = ctx.source_report or {}
        sources = report.get("sources", [])
        rec = report.get("reconciliation", {})

        if sources:
            ok = [s["name"] for s in sources if s["ok"]]
            off = [f"{s['name']}({s['error']})" for s in sources if not s["ok"]]
            checks.append(self._check(
                "데이터 출처", GOOD if len(ok) >= 2 else WARN if ok else BAD,
                f"응답 {len(ok)}/{len(sources)}: {', '.join(ok) or '없음'}" + (f" · 미사용: {', '.join(off)}" if off else "")))

        if rec.get("results"):
            share = rec["confirmed"] / rec["results"]
            checks.append(self._check(
                "결과 교차 확인", GOOD if share >= 0.8 else WARN if share > 0 else WARN,
                f"종료 {rec['results']}경기 중 {rec['confirmed']}경기가 2개 이상 출처에서 일치"
                + ("" if share else " (출처 1개뿐이라 교차 확인 불가)")))
        conflicts = rec.get("conflicts", [])
        checks.append(self._check(
            "출처 간 스코어 불일치", GOOD if not conflicts else WARN,
            "없음" if not conflicts else f"{len(conflicts)}경기 — 우선순위 높은 출처의 결과를 채택"))

        now = datetime.now(timezone.utc)
        cutoff = datetime.combine(ctx.target_date, datetime.min.time(), tzinfo=timezone.utc)
        horizon = min(now, cutoff)
        stale = [m for m in ctx.all_matches
                 if m.home.current and m.utc_date < horizon and (horizon - m.utc_date).total_seconds() > 3 * 3600
                 and not m.finished and m.status != "POSTPONED"]
        checks.append(self._check(
            "결과 반영 지연", GOOD if not stale else WARN if len(stale) <= 10 else BAD,
            "지난 경기 결과가 모두 반영됨" if not stale else
            f"지난 경기 {len(stale)}개의 결과가 아직 없음 → 해당 경기는 학습에서 빠짐 (최근 결과는 키가 있는 출처가 빠름)"))

        involved = {m.home.id for m in ctx.fixtures} | {m.away.id for m in ctx.fixtures}
        with_players = [t for t in involved if ctx.players.get(t)]
        if involved:
            checks.append(self._check(
                "선수·부상 데이터", GOOD if len(with_players) == len(involved) else WARN if with_players else BAD,
                f"경기 팀 {len(involved)}곳 중 {len(with_players)}곳" +
                ("" if with_players else " — 선수 보정 없이 팀 전력만으로 예측")))

        injury_sources = [s["name"] for s in sources if s["ok"] and s.get("counts", {}).get("injuries")]
        manual = any(p.play_prob < 1 for ps in ctx.players.values() for p in ps)
        injury_data = bool(injury_sources) or manual
        if sources:
            checks.append(self._check(
                "부상·결장 정보", GOOD if injury_sources else WARN,
                f"출처: {', '.join(injury_sources)}"
                + (" (비공식 출처 — 공식 발표와 다를 수 있음)" if "Fantasy Premier League" in injury_sources else "")
                if injury_sources else
                ("수동 입력(data/availability.json)만 반영" if manual else
                 "이번 시즌 부상 정보를 주는 무료 출처 없음 — data/availability.json 에 직접 입력 가능")))

        levels = [c["status"] for c in checks]
        overall = BAD if BAD in levels else WARN if WARN in levels else GOOD
        ctx.data_quality = {"overall": overall, "checks": checks, "conflicts": conflicts[:20],
                            "stale_matches": len(stale), "injury_data": injury_data}
        ko = {GOOD: "양호", WARN: "주의", BAD: "부족"}
        issues = [f"{c['label']} {ko[c['status']]}" for c in checks if c["status"] != GOOD]
        return f"데이터 품질 {ko[overall]}" + (f": {', '.join(issues)}" if issues else "")

    @staticmethod
    def _check(label: str, status: str, detail: str) -> dict:
        return {"label": label, "status": status, "detail": detail}
