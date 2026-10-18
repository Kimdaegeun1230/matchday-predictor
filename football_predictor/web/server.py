"""표준 라이브러리만 쓰는 경량 웹 서버.

GET /                                  예측 화면 (static/index.html)
GET /api/leagues                       지원 리그 목록
GET /api/calendar?league=EPL           이번 시즌 경기일 목록
GET /api/next-date?league=EPL          다음 경기일
GET /api/predictions?league=EPL&date=YYYY-MM-DD
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..agents.coordinator import CoordinatorAgent
from ..config import LEAGUES

STATIC_DIR = Path(__file__).parent / "static"
log = logging.getLogger("web")


def make_handler(coordinator: CoordinatorAgent):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload) -> None:
            self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path in ("/", "/index.html"):
                    self._send(200, (STATIC_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
                elif url.path == "/api/leagues":
                    self._json(200, [{"key": l.key, "name": l.name, "country": l.country} for l in LEAGUES.values()])
                elif url.path == "/api/calendar":
                    self._json(200, coordinator.match_calendar(q.get("league", "EPL"), date.today()))
                elif url.path == "/api/next-date":
                    nxt = coordinator.next_match_date(q.get("league", "EPL"), date.today())
                    self._json(200, {"date": nxt.isoformat() if nxt else None})
                elif url.path == "/api/predictions":
                    target = date.fromisoformat(q.get("date") or date.today().isoformat())
                    self._json(200, coordinator.predict(q.get("league", "EPL"), target))
                else:
                    self._json(404, {"error": "not found"})
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:  # 데이터 공급자 오류 등
                log.exception("요청 처리 실패")
                self._json(502, {"error": f"{type(exc).__name__}: {exc}"})

        def log_message(self, fmt, *args) -> None:
            log.info("%s - %s", self.address_string(), fmt % args)

    return Handler


class _Server(ThreadingHTTPServer):
    # Windows에서 SO_REUSEADDR는 이미 사용 중인 포트에 두 번째 서버가 붙는 것을 허용하므로 끈다
    allow_reuse_address = os.name != "nt"


def serve(host: str = "127.0.0.1", port: int = 8000, source: str = "auto") -> None:
    coordinator = CoordinatorAgent(source=source, write_files=False)
    try:
        httpd = _Server((host, port), make_handler(coordinator))
    except OSError as exc:
        raise SystemExit(f"포트 {port} 를 열 수 없습니다 (이미 실행 중인 서버가 있는지 확인): {exc}") from exc
    print(f"웹 서버 실행 중: http://{host}:{port}  (데이터: {coordinator.provider.name})  Ctrl+C 로 종료")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
