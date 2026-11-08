"""API 키 없이 매체 예측을 모으는 경로: 예약 실행되는 Claude(이 PC의 Claude 앱)가 직접 검색한다.

  1. media-due    → 수집할 경기 목록을 JSON 으로 내보냄 (fixture_id 포함)
  2. (Claude 가 영국·국내 매체를 검색해 found.json 작성)
  3. media-import → found.json 을 검사(허용 매체 링크, 알려진 경기, 스코어 범위)해 media.json 에 합침

검사 규칙은 API 경로(MediaCollector)와 같은 함수를 쓴다.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from ..config import MEDIA_DOMAINS, MEDIA_FILE, NBA_MEDIA_DOMAINS, get_league
from .collector import MediaCollector
from .store import MediaStore


def export_due(coordinator, league_key: str, out_path: Path, max_days: int = 4,
               store_path: Path = MEDIA_FILE) -> dict:
    league = get_league(league_key)
    if league.sport == "nba":
        return _export_due_nba(coordinator.engine(league.key), out_path, max_days)
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
        "sport": "football",
        "allowed_domains": MEDIA_DOMAINS,
        "fixtures": fixtures,
        "found_format": [{"fixture_id": "위 목록의 fixture_id", "outlet": "BBC Sport", "author": "Chris Sutton",
                          "home_goals": 2, "away_goals": 1, "url": "https://www.bbc.co.uk/sport/...",
                          "headline": "기사 제목"}],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"fixtures": len(fixtures), "days": len(groups)}


NBA_MAX_FIXTURES = 20  # 하루 15경기까지 있어 한 번에 다룰 수를 제한


def _export_due_nba(engine, out_path: Path, max_days: int) -> dict:
    """NBA: 오늘~max_days 일 경기 + 최근 7일 끝난 경기 중 아직 안 모은 것. 점수는 home_goals/away_goals 칸에 그대로 넣는다."""
    teams = engine.data().teams
    name = lambda k: teams[k].name if k in teams else k
    games = engine.media_due(date.today(), max_days)[:NBA_MAX_FIXTURES]  # 다가오는 경기 먼저
    fixtures = [{"fixture_id": str(g.id), "home": name(g.home), "away": name(g.away), "game_date_us": g.date.isoformat(),
                 "kickoff_utc": g.tipoff.isoformat(timespec="minutes") if g.tipoff else g.date.isoformat(),
                 "finished": g.finished, "result": f"{g.home_pts}-{g.away_pts}" if g.finished else None}
                for g in games]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "league": "NBA",
        "sport": "nba",
        "allowed_domains": NBA_MEDIA_DOMAINS,
        "fixtures": fixtures,
        "found_format": [{"fixture_id": "위 목록의 fixture_id", "outlet": "Covers", "author": "기자 이름",
                          "home_goals": 112, "away_goals": 105, "url": "https://www.covers.com/...",
                          "headline": "기사 제목 (점수 칸에 홈/원정 예상 득점)"}],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"fixtures": len(fixtures), "days": len({g.date for g in games})}


def import_found(due_path: Path, found_path: Path, store_path: Path | None = None) -> dict:
    due = json.loads(due_path.read_text(encoding="utf-8"))
    nba = due.get("sport") == "nba"
    if store_path is None:
        from ..nba.engine import NBA_MEDIA_FILE
        store_path = NBA_MEDIA_FILE if nba else MEDIA_FILE
    fixtures = [{"id": f["fixture_id"], "home": f["home"], "away": f["away"], "kickoff": f["kickoff_utc"]}
                for f in due.get("fixtures", [])]
    raw = json.loads(found_path.read_text(encoding="utf-8")) if found_path.exists() else []
    if isinstance(raw, dict):
        raw = raw.get("predictions", [])
    cleaned = (MediaCollector._clean({"predictions": raw}, fixtures, NBA_MEDIA_DOMAINS, (60, 180)) if nba
               else MediaCollector._clean({"predictions": raw}, fixtures))
    store = MediaStore(store_path)
    added = store.add([f["id"] for f in fixtures], cleaned)  # 찾은 게 없어도 '수집함'으로 기록 → 같은 경기 반복 검색 방지
    store.save()
    return {"submitted": len(raw), "accepted": len(cleaned), "new": added, "rejected": len(raw) - len(cleaned),
            "fixtures": len(fixtures), "total": len(store.predictions)}
