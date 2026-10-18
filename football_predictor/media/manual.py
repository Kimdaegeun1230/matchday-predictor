"""API 키 없이 매체 예측을 모으는 경로: 예약 실행되는 Claude(이 PC의 Claude 앱)가 직접 검색한다.

  1. media-due    → 수집할 경기 목록을 JSON 으로 내보냄 (fixture_id 포함)
  2. (Claude 가 영국 매체를 검색해 found.json 작성)
  3. media-import → found.json 을 검사(허용 매체 링크, 알려진 경기, 스코어 범위)해 media.json 에 합침

검사 규칙은 API 경로(MediaCollector)와 같은 함수를 쓴다.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from ..config import MEDIA_DOMAINS, MEDIA_FILE, get_league
from .collector import MediaCollector
from .store import MediaStore


def export_due(coordinator, league_key: str, out_path: Path, max_days: int = 4,
               store_path: Path = MEDIA_FILE) -> dict:
    league = get_league(league_key)
    store = MediaStore(store_path)
    matches = coordinator.provider.get_matches(league, date.today())
    groups = store.group_by_day(store.due_fixtures(matches))[:max_days]
    fixtures = [{"fixture_id": str(m.id), "home": m.home.name, "away": m.away.name,
                 "kickoff_utc": m.utc_date.isoformat(timespec="minutes"), "finished": m.finished,
                 "result": f"{m.home_goals}-{m.away_goals}" if m.finished else None}
                for g in groups for m in g]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "league": league.name,
        "allowed_domains": MEDIA_DOMAINS,
        "fixtures": fixtures,
        "found_format": [{"fixture_id": "위 목록의 fixture_id", "outlet": "BBC Sport", "author": "Chris Sutton",
                          "home_goals": 2, "away_goals": 1, "url": "https://www.bbc.co.uk/sport/...",
                          "headline": "기사 제목"}],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"fixtures": len(fixtures), "days": len(groups)}


def import_found(due_path: Path, found_path: Path, store_path: Path = MEDIA_FILE) -> dict:
    due = json.loads(due_path.read_text(encoding="utf-8"))
    fixtures = [{"id": f["fixture_id"], "home": f["home"], "away": f["away"], "kickoff": f["kickoff_utc"]}
                for f in due.get("fixtures", [])]
    raw = json.loads(found_path.read_text(encoding="utf-8")) if found_path.exists() else []
    if isinstance(raw, dict):
        raw = raw.get("predictions", [])
    cleaned = MediaCollector._clean({"predictions": raw}, fixtures)
    store = MediaStore(store_path)
    added = store.add([f["id"] for f in fixtures], cleaned)  # 찾은 게 없어도 '수집함'으로 기록 → 같은 경기 반복 검색 방지
    store.save()
    return {"submitted": len(raw), "accepted": len(cleaned), "new": added, "rejected": len(raw) - len(cleaned),
            "fixtures": len(fixtures), "total": len(store.predictions)}
