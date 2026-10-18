"""공급자 공통: 파일 캐시를 거치는 JSON GET."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..config import CACHE_DIR, CACHE_TTL_SECONDS

log = logging.getLogger(__name__)
USER_AGENT = "matchday-predictor/0.2 (+personal analysis)"


class ApiError(RuntimeError):
    pass


def cached_get_json(url: str, headers: dict[str, str] | None = None, ttl: int = CACHE_TTL_SECONDS) -> dict | list:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".json")
    # 같은 URL을 동시에 요청하면(웹 서버 스레드) 한 번만 내려받는다
    with _url_lock(url):
        if cache_file.exists() and time.time() - cache_file.stat().st_mtime < ttl:
            try:
                return json.loads(cache_file.read_text(encoding="utf-8"))
            except ValueError:
                log.warning("손상된 캐시 파일 무시: %s", cache_file.name)
        return _fetch(url, headers, cache_file)


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _url_lock(url: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(url, threading.Lock())


def _fetch(url: str, headers: dict[str, str] | None, cache_file: Path) -> dict | list:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            # 임시 파일에 쓴 뒤 교체해, 읽는 쪽이 쓰다 만 파일을 보지 않게 한다
            tmp = cache_file.with_suffix(f".{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, cache_file)
            return data
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 2:
                wait = int(exc.headers.get("X-RequestCounter-Reset", "10") or 10)
                log.warning("요청 제한(429). %ss 대기 후 재시도", wait)
                time.sleep(min(wait, 60))
                continue
            raise ApiError(f"{url} -> HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise ApiError(f"{url} -> {exc.reason}") from exc
        except (TimeoutError, OSError) as exc:  # 응답 읽기 시간 초과, 연결 끊김
            if attempt < 1:
                log.warning("응답 지연, 재시도: %s", url)
                continue
            raise ApiError(f"{url} -> 응답 시간 초과 ({exc})") from exc
    raise ApiError(f"{url} -> 재시도 초과")
