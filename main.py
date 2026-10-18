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


def print_accuracy(acc: dict, latest_only: bool = False) -> int:
    pct = lambda x: "-" if x is None else f"{x:.0%}"
    rounds = acc["rounds"]
    if latest_only:
        rounds = [r for r in rounds if r["matchday"] == acc["latest_complete_round"]]
    print(f"{'라운드':>6} {'상태':<6} {'경기':>4} {'결과 적중':>10} {'스코어 적중':>12} {'(최빈 스코어)':>12}")
    for r in rounds:
        state = "완료" if r["complete"] else f"{r['finished']}/{r['matches']}"
        print(f"{r['matchday']:>6} {state:<6} {r['matches']:>4} "
              f"{r['outcome_hits']:>3}/{r['finished']:<2} {pct(r['outcome_rate']):>4}   "
              f"{r['exact_hits']:>3}/{r['finished']:<2} {pct(r['exact_rate']):>4}    "
              f"{r['mode_exact_hits']:>3}/{r['finished']:<2} {pct(r['mode_exact_rate']):>4}")
        if latest_only:
            for i in r["items"]:
                mark = "✓" if i["outcome_hit"] else "✗"
                exact = " (스코어 적중)" if i["exact_hit"] else ""
                print(f"    {i['home']} {i['actual']} {i['away']}  예측 {i['predicted']} (최빈 {i['mode_predicted']})  {mark}{exact}")
    s = acc["season"]
    print(f"\n시즌 누적 {s['finished']}경기: 결과 적중 {s['outcome_hits']} ({pct(s['outcome_rate'])}), "
          f"스코어 적중 {s['exact_hits']} ({pct(s['exact_rate'])}) · 최빈 스코어였다면 {s['mode_exact_hits']} ({pct(s['mode_exact_rate'])})")
    if s["media_matches"]:
        print(f"매체 예측이 있던 {s['media_matches']}경기: 모델만 {pct(s['model_only_rate'])} → 매체 반영 후 {pct(s['with_media_rate'])}")
    return 0


def media_restore(site_url: str) -> int:
    """GitHub Actions 는 매번 빈 상태로 시작하므로, 지난 배포에 올려 둔 매체 예측 기록을 받아 이어서 쌓는다."""
    import json
    import urllib.error
    import urllib.request
    from football_predictor.config import MEDIA_FILE
    if MEDIA_FILE.exists():
        print("매체 기록이 이미 있어 내려받지 않음")
        return 0
    url = site_url.rstrip("/") + "/data/media.json"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        print(f"매체 기록 없음 (처음 실행이면 정상): {exc}")
        return 0
    MEDIA_FILE.parent.mkdir(parents=True, exist_ok=True)
    MEDIA_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"매체 기록 복원: 예측 {len(data.get('predictions', []))}건")
    return 0


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

    a = sub.add_parser("accuracy", help="라운드별 예측 적중률 (결과 적중 / 스코어 적중)")
    a.add_argument("--latest", action="store_true", help="가장 최근에 끝난 라운드만")

    m = sub.add_parser("collect-media", help="영국 현지 매체 예측 수집 (Claude, ANTHROPIC_API_KEY 필요)")
    m.add_argument("--max-calls", type=int, default=None, help="이번 실행의 Claude 호출 상한")

    d = sub.add_parser("media-due", help="매체 예측을 수집할 경기 목록을 JSON 으로 내보내기 (예약 실행용)")
    d.add_argument("--out", default="data/media/due.json")
    d.add_argument("--max-days", type=int, default=4, help="한 번에 다룰 경기 날짜 수")

    i = sub.add_parser("media-import", help="찾은 매체 예측(JSON)을 검사해 기록에 합치기 (예약 실행용)")
    i.add_argument("found", help="찾은 예측 JSON 파일")
    i.add_argument("--due", default="data/media/due.json")

    r = sub.add_parser("media-restore", help="배포된 사이트에서 매체 예측 기록을 내려받기 (없을 때만)")
    r.add_argument("--from", dest="site_url", required=True, help="사이트 주소 (예: https://아이디.github.io/저장소/)")

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

    if args.cmd == "accuracy":
        return print_accuracy(CoordinatorAgent(write_files=False).round_accuracy("EPL"), args.latest)

    if args.cmd == "collect-media":
        coordinator = CoordinatorAgent(write_files=False)
        for key in LEAGUES:
            r = coordinator.collect_media(key, max_calls=args.max_calls)
            if r.get("skipped"):
                print(f"{key}: 매체 수집 건너뜀 — {r['skipped']}")
            else:
                u = r.get("usage", {})
                print(f"{key}: Claude 호출 {r['calls']}회, 대상 {r.get('matches', 0)}경기, 예측 {r['found']}건"
                      + (f" (입력 {u.get('input_tokens', 0):,} / 출력 {u.get('output_tokens', 0):,} 토큰,"
                         f" 웹 검색 {u.get('web_searches', 0)}회)" if u else ""))
        return 0

    if args.cmd == "media-due":
        from pathlib import Path
        from football_predictor.media.manual import export_due
        r = export_due(CoordinatorAgent(write_files=False), "EPL", Path(args.out), args.max_days)
        print(f"수집할 경기 {r['fixtures']}개 ({r['days']}일치) → {args.out}")
        return 0

    if args.cmd == "media-import":
        from pathlib import Path
        from football_predictor.media.manual import import_found
        r = import_found(Path(args.due), Path(args.found))
        print(f"제출 {r['submitted']}건 중 {r['accepted']}건 반영 (새 예측 {r['new']}건, 제외 {r['rejected']}건)"
              f" · 대상 경기 {r['fixtures']}개 · 누적 {r['total']}건")
        return 0

    if args.cmd == "media-restore":
        return media_restore(args.site_url)

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
