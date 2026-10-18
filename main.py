"""CLI 진입점.

예)
  python main.py check-sources                # 출처별 연결 상태 점검 (키 설정 후 먼저 실행)
  python main.py predict                      # 다음 경기일
  python main.py predict --date 2026-10-18
  python main.py predict --source sample
  python main.py serve --port 8000
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from football_predictor.agents.coordinator import SOURCES, CoordinatorAgent
from football_predictor.config import LEAGUES


def print_report(report: dict) -> None:
    print(f"\n=== {report['league_name']} | {report['date']} | 데이터: {report['data_source']} ===")
    if not report["predictions"]:
        print("  이 날짜에 예정된 경기가 없습니다.")
    for p in report["predictions"]:
        print(f"  {p['home_team']:>28}  {p['predicted_score']:^5}  {p['away_team']:<28}"
              f" xG {p['home_xg']:.2f}-{p['away_xg']:.2f} | "
              f"H {p['home_win']:.0%} D {p['draw']:.0%} A {p['away_win']:.0%} | 신뢰도 {p['confidence']}")


def check_sources() -> int:
    coordinator = CoordinatorAgent(write_files=False)
    for key in LEAGUES:
        try:
            status = coordinator.source_status(key, date.today())
        except Exception as exc:  # 모든 출처 실패
            print(f"{key}: 실패 — {exc}")
            return 1
        print(f"\n=== {key} 데이터 출처 ===")
        for s in status["sources"]:
            mark = "OK " if s["ok"] else ("-- " if not s["enabled"] else "ERR")
            print(f"[{mark}] {s['name']:<20} {s['license']}")
            if s["ok"]:
                c = s["counts"]
                print(f"       팀 {c['teams']} · 경기 {c['matches']} (결과 {c['results']}) · 직전 시즌 {c['previous_season']}"
                      f" · 선수 {c['players']} · 결장/불확실 {c['injuries']}")
            if s["error"]:
                print(f"       {s['error']}")
            for n in s["notes"]:
                print(f"       참고: {n}")
        r = status["reconciliation"]
        print(f"\n병합: 이번 시즌 {r['matches']}경기, 결과 {r['results']} "
              f"(교차 확인 {r['confirmed']}, 단일 출처 {r['single_source']}, 불일치 {len(r['conflicts'])})")
        for c in r["conflicts"][:10]:
            print(f"  불일치 {c['date']} {c['home']} - {c['away']}: {c['scores']} → {c['chosen']} 채택")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="프리미어리그 경기일 스코어 예측 에이전트")
    parser.add_argument("-v", "--verbose", action="store_true", help="에이전트 실행 로그 출력")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("predict", help="경기일 스코어 예측")
    p.add_argument("--league", default="all", help=f"{', '.join(LEAGUES)} 또는 all")
    p.add_argument("--date", default="next", help="YYYY-MM-DD 또는 next(다음 경기일, 기본값)")
    p.add_argument("--source", default="auto", choices=SOURCES)

    sub.add_parser("check-sources", help="데이터 출처별 연결 상태와 병합 결과 점검")

    b = sub.add_parser("build", help="정적 사이트 빌드 (GitHub Pages 등에 그대로 올림)")
    b.add_argument("--out", default="site", help="출력 폴더 (기본: site)")
    b.add_argument("--domain", default="", help="커스텀 도메인 (CNAME 파일 생성, 예: matchdaypredictor.com)")
    b.add_argument("--source", default="auto", choices=SOURCES)

    s = sub.add_parser("serve", help="웹 서버 실행")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--source", default="auto", choices=SOURCES)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")

    if args.cmd == "serve":
        from football_predictor.web.server import serve
        serve(args.host, args.port, args.source)
        return 0

    if args.cmd == "check-sources":
        return check_sources()

    if args.cmd == "build":
        from pathlib import Path
        from football_predictor.web.build import build_site
        summary = build_site(Path(args.out), args.source, args.domain or None)
        for key, s in summary["leagues"].items():
            print(f"{key}: 경기일 {s['dates']}개, 경기 {s['matches']}개")
        print(f"빌드 완료: {args.out}/ ({summary['seconds']}s)")
        return 0

    coordinator = CoordinatorAgent(source=args.source)
    keys = list(LEAGUES) if args.league.lower() == "all" else [args.league.upper()]
    for key in keys:
        if args.date == "next":
            target = coordinator.next_match_date(key, date.today())
            if target is None:
                print(f"{key}: 남은 경기가 없습니다.")
                continue
        else:
            target = date.fromisoformat(args.date)
        print_report(coordinator.predict(key, target))
    print("\n결과 JSON: output/ 폴더")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
