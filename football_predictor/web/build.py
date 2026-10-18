"""정적 사이트 빌드: 서버 없이 올릴 수 있는 파일 묶음을 만든다 (GitHub Pages 등).

site/
  index.html                         웹 화면 (정적 모드 표시가 들어감)
  data/leagues.json                  리그 목록
  data/meta.json                     빌드 시각
  data/EPL/calendar.json             경기일 목록
  data/EPL/predictions/2026-10-18.json   경기일별 예측 리포트
  CNAME                              (선택) 커스텀 도메인
  .nojekyll                          GitHub Pages 가 파일을 가공하지 않게
"""
from __future__ import annotations

import json
import logging
import shutil
import time
from datetime import date, datetime, timezone
from pathlib import Path

from ..agents.coordinator import CoordinatorAgent
from ..config import LEAGUES

STATIC_DIR = Path(__file__).parent / "static"
STATIC_MARKER = "<!--MP_STATIC-->"
log = logging.getLogger("build")


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def build_site(out_dir: Path, source: str = "auto", domain: str | None = None, today: date | None = None) -> dict:
    today = today or date.today()
    started = time.time()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    coordinator = CoordinatorAgent(source=source, write_files=False)
    summary = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "leagues": {}}
    for key, league in LEAGUES.items():
        calendar = coordinator.match_calendar(key, today)
        _write(out_dir / "data" / key / "calendar.json", calendar)
        for day in calendar:
            report = coordinator.predict(key, date.fromisoformat(day["date"]))
            _write(out_dir / "data" / key / "predictions" / f"{day['date']}.json", report)
        summary["leagues"][key] = {"dates": len(calendar), "matches": sum(d["matches"] for d in calendar)}
        log.info("%s: 경기일 %d개 빌드", key, len(calendar))

    # 매체 예측 기록도 함께 올려 둔다 → 다음 빌드(GitHub Actions)가 내려받아 이어서 쌓음 (적중률 유지)
    if coordinator.media_path.exists():
        shutil.copy(coordinator.media_path, out_dir / "data" / "media.json")

    _write(out_dir / "data" / "leagues.json",
           [{"key": l.key, "name": l.name, "country": l.country} for l in LEAGUES.values()])
    _write(out_dir / "data" / "meta.json", summary)

    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    if STATIC_MARKER not in html:
        raise RuntimeError("index.html 에 정적 모드 표시 자리가 없습니다")
    html = html.replace(STATIC_MARKER, "<script>window.MP_STATIC = true;</script>")
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")
    if domain:
        (out_dir / "CNAME").write_text(domain.strip() + "\n", encoding="utf-8")

    summary["seconds"] = round(time.time() - started, 1)
    return summary
