"""NBA 주간 적중률. 정확한 점수는 거의 맞을 수 없어 '근접'(두 팀 점수 모두 ±5 이내)과 평균 오차를 함께 본다."""
from __future__ import annotations

from datetime import date, timedelta


def _pts(score: str) -> tuple[int, int]:
    h, a = score.split("-")
    return int(h), int(a)


def _rate(hits: int, n: int) -> float | None:
    return round(hits / n, 4) if n else None


def nba_accuracy(reports: list[dict]) -> dict:
    weeks: dict[date, dict] = {}
    seen: set[int] = set()
    for r in sorted(reports, key=lambda r: r["date"]):
        for p in r["predictions"]:
            if p["match_id"] in seen:
                continue
            seen.add(p["match_id"])
            d = date.fromisoformat(p.get("date") or r["date"])
            monday = d - timedelta(days=d.weekday())
            w = weeks.setdefault(monday, {"week_start": monday.isoformat(),
                                          "week_end": (monday + timedelta(days=6)).isoformat(),
                                          "label": f"{monday:%m/%d}~{monday + timedelta(days=6):%m/%d}",
                                          "matches": 0, "finished": 0, "outcome_hits": 0, "exact_hits": 0,
                                          "close_hits": 0, "abs_err_sum": 0.0, "margin_err_sum": 0.0,
                                          "media_matches": 0, "model_only_hits": 0, "with_media_hits": 0,
                                          "items": []})
            w["matches"] += 1
            item = {"date": d.isoformat(), "kickoff": p["kickoff"], "home": p["home_team"], "away": p["away_team"],
                    "predicted": p["predicted_score"], "actual": p.get("actual_score"),
                    "outcome_hit": None, "exact_hit": None, "close_hit": None, "abs_err": None,
                    "media_count": (p.get("media") or {}).get("count", 0)}
            if p.get("actual_score"):
                ph, pa = _pts(p["predicted_score"])
                ah, aa = _pts(p["actual_score"])
                w["finished"] += 1
                item["outcome_hit"] = (ph > pa) == (ah > aa)
                item["exact_hit"] = (ph, pa) == (ah, aa)
                item["close_hit"] = abs(ph - ah) <= 5 and abs(pa - aa) <= 5
                item["abs_err"] = (abs(ph - ah) + abs(pa - aa)) / 2
                w["outcome_hits"] += item["outcome_hit"]
                w["exact_hits"] += item["exact_hit"]
                w["close_hits"] += item["close_hit"]
                w["abs_err_sum"] += item["abs_err"]
                w["margin_err_sum"] += abs((ph - pa) - (ah - aa))
                m = p.get("media") or {}
                if m.get("count"):
                    w["media_matches"] += 1
                    w["model_only_hits"] += (m["model_only"]["H"] >= 0.5) == (ah > aa)
                    w["with_media_hits"] += item["outcome_hit"]
            w["items"].append(item)

    rows = []
    for k in sorted(weeks):
        w = weeks[k]
        n = w["finished"]
        w["complete"] = n > 0 and n == w["matches"]
        w["outcome_rate"] = _rate(w["outcome_hits"], n)
        w["exact_rate"] = _rate(w["exact_hits"], n)
        w["close_rate"] = _rate(w["close_hits"], n)
        w["mean_abs_err"] = round(w.pop("abs_err_sum") / n, 1) if n else None
        w["margin_mae"] = round(w.pop("margin_err_sum") / n, 1) if n else None
        w["items"].sort(key=lambda i: i["kickoff"])
        rows.append(w)
    fin = sum(w["finished"] for w in rows)
    season = {k: sum(w[k] for w in rows) for k in ("finished", "outcome_hits", "exact_hits", "close_hits",
                                                   "media_matches", "model_only_hits", "with_media_hits")}
    errs = [i["abs_err"] for w in rows for i in w["items"] if i["abs_err"] is not None]
    season.update(outcome_rate=_rate(season["outcome_hits"], fin), exact_rate=_rate(season["exact_hits"], fin),
                  close_rate=_rate(season["close_hits"], fin),
                  mean_abs_err=round(sum(errs) / len(errs), 1) if errs else None,
                  model_only_rate=_rate(season["model_only_hits"], season["media_matches"]),
                  with_media_rate=_rate(season["with_media_hits"], season["media_matches"]),
                  complete_rounds=sum(w["complete"] for w in rows))
    done = [w for w in rows if w["complete"]]
    return {"sport": "nba", "rounds": rows, "season": season,
            "latest_complete_round": done[-1]["label"] if done else None}
