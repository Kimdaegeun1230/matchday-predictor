"""라운드별 예측 적중률: 경기일마다 '그날 이전 데이터로만' 만든 예측을 실제 결과와 비교한다.

- 결과 적중: 승/무/패를 맞힘 (대표 스코어 기준)
- 스코어 적중: 대표 스코어가 정확히 일치 (비교용으로 최빈 스코어도 따로 채점)
- 매체 반영 효과: 매체 예측이 있던 경기에서 '모델만'의 승무패 판단도 따로 채점해 비교
라운드의 모든 경기가 끝나야 '완료'로 본다 (연기된 경기가 있으면 그 경기가 끝날 때까지 진행 중).
"""
from __future__ import annotations

from .agents.prediction_agent import predicted_outcome


def _outcome(score: str) -> str:
    h, a = map(int, score.split("-"))
    return "H" if h > a else "D" if h == a else "A"


def _rate(hits: int, n: int) -> float | None:
    return round(hits / n, 4) if n else None


def round_accuracy(reports: list[dict]) -> dict:
    """reports: 경기일별 예측 리포트(build_report 결과) 목록."""
    rounds: dict[int, dict] = {}
    seen: set[int] = set()
    for r in sorted(reports, key=lambda r: r["date"]):
        for p in r["predictions"]:
            if p["match_id"] in seen:
                continue
            seen.add(p["match_id"])
            md = p.get("matchday") or 0
            row = rounds.setdefault(md, {"matchday": md, "matches": 0, "finished": 0, "outcome_hits": 0,
                                         "exact_hits": 0, "mode_exact_hits": 0, "media_matches": 0, "model_only_hits": 0,
                                         "with_media_hits": 0, "first_date": r["date"], "last_date": r["date"],
                                         "items": []})
            row["matches"] += 1
            row["last_date"] = max(row["last_date"], r["date"])
            item = {"date": r["date"], "kickoff": p["kickoff"], "home": p["home_team"], "away": p["away_team"],
                    "home_tla": p.get("home_tla"), "away_tla": p.get("away_tla"),
                    "predicted": p["predicted_score"], "mode_predicted": p.get("mode_score"),
                    "actual": p.get("actual_score"), "mode_exact_hit": None,
                    "probs": {"H": p["home_win"], "D": p["draw"], "A": p["away_win"]},
                    "outcome_hit": None, "exact_hit": None, "media_count": (p.get("media") or {}).get("count", 0)}
            actual = p.get("actual_score")
            if actual:
                row["finished"] += 1
                item["outcome_hit"] = _outcome(p["predicted_score"]) == _outcome(actual)
                item["exact_hit"] = p["predicted_score"] == actual
                row["outcome_hits"] += item["outcome_hit"]
                row["exact_hits"] += item["exact_hit"]
                if p.get("mode_score"):
                    item["mode_exact_hit"] = p["mode_score"] == actual
                    row["mode_exact_hits"] += item["mode_exact_hit"]
                media = p.get("media") or {}
                if media.get("count"):
                    mo = media["model_only"]
                    model_pick = predicted_outcome(mo["H"], mo["D"], mo["A"])
                    row["media_matches"] += 1
                    row["model_only_hits"] += model_pick == _outcome(actual)
                    row["with_media_hits"] += item["outcome_hit"]
            row["items"].append(item)

    out_rounds = []
    for md in sorted(rounds):
        row = rounds[md]
        row["complete"] = row["matches"] > 0 and row["finished"] == row["matches"]
        row["outcome_rate"] = _rate(row["outcome_hits"], row["finished"])
        row["exact_rate"] = _rate(row["exact_hits"], row["finished"])
        row["mode_exact_rate"] = _rate(row["mode_exact_hits"], row["finished"])
        row["items"].sort(key=lambda i: i["kickoff"])
        out_rounds.append(row)

    done = [r for r in out_rounds if r["complete"]]
    fin = sum(r["finished"] for r in out_rounds)
    season = {
        "finished": fin,
        "outcome_hits": sum(r["outcome_hits"] for r in out_rounds),
        "exact_hits": sum(r["exact_hits"] for r in out_rounds),
        "mode_exact_hits": sum(r["mode_exact_hits"] for r in out_rounds),
        "complete_rounds": len(done),
        "media_matches": sum(r["media_matches"] for r in out_rounds),
        "model_only_hits": sum(r["model_only_hits"] for r in out_rounds),
        "with_media_hits": sum(r["with_media_hits"] for r in out_rounds),
    }
    season["outcome_rate"] = _rate(season["outcome_hits"], fin)
    season["exact_rate"] = _rate(season["exact_hits"], fin)
    season["mode_exact_rate"] = _rate(season["mode_exact_hits"], fin)
    season["model_only_rate"] = _rate(season["model_only_hits"], season["media_matches"])
    season["with_media_rate"] = _rate(season["with_media_hits"], season["media_matches"])
    latest = done[-1]["matchday"] if done else None
    return {"rounds": out_rounds, "season": season, "latest_complete_round": latest}
