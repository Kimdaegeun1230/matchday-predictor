"""영국 현지 매체의 경기 스코어 예측을 Claude + 웹 검색으로 수집한다.

한 번의 호출에 같은 날짜의 경기 여러 개를 묶어서 보낸다.
Claude 가 허용된 영국 매체 도메인만 검색해, 실제로 찾은 예측만 record_predictions 도구로 돌려준다.
기사 본문은 저장하지 않고 (매체, 작성자, 예측 스코어, 링크, 제목)만 남긴다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

from ..config import MEDIA, MEDIA_DOMAINS, MEDIA_EFFORT, MEDIA_MODEL, MediaConfig

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_CONTINUATIONS = 5

SYSTEM_PROMPT = """You collect English Premier League score predictions published by UK media outlets.

For each fixture you are given, search the allowed UK outlets for articles in which a named pundit, columnist or \
the outlet itself predicts the final score of that match (for example weekly prediction columns, match previews \
with a "prediction" line, or pundit-versus-guest prediction features).

Rules:
- Record only predictions you actually read on a page from an allowed outlet. Never infer, average or invent a score.
- One entry per (fixture, outlet, author). Use the author's name as printed; use the outlet name as the author when \
the prediction is unsigned.
- Use the page URL where the prediction appears and its headline.
- Skip betting tips without a specific scoreline, and skip predictions for other matches.
- Record human predictions only. Skip AI or statistical-model outputs (e.g. "AI predictor", "supercomputer",
  data-model win probabilities), because the app already has its own model.
- Per-match preview pages usually carry the scoreline in a closing "prediction" or "We say:" line \
(Sports Mole, WhoScored, Squawka, Sporting Life); weekly columns cover many matches at once (Sky Sports "Jones Knows").
- When you have finished searching, call record_predictions exactly once with everything you found. If you found \
nothing, call it with an empty list."""


def _record_tool(fixture_ids: list[str]) -> dict:
    return {
        "name": "record_predictions",
        "description": "Save the scoreline predictions found in UK media for the given fixtures. "
                       "Call exactly once, after searching, with every prediction found (empty list if none).",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "predictions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "fixture_id": {"type": "string", "enum": fixture_ids},
                            "outlet": {"type": "string", "description": "Outlet name, e.g. BBC Sport"},
                            "author": {"type": "string", "description": "Pundit or columnist name"},
                            "home_goals": {"type": "integer"},
                            "away_goals": {"type": "integer"},
                            "url": {"type": "string"},
                            "headline": {"type": "string"},
                        },
                        "required": ["fixture_id", "outlet", "author", "home_goals", "away_goals", "url", "headline"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["predictions"],
            "additionalProperties": False,
        },
    }


def _allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in MEDIA_DOMAINS)


class MediaCollector:
    """fixtures: [{"id": "123", "home": "Arsenal", "away": "Chelsea", "kickoff": "2026-10-18T14:00:00+00:00"}]"""

    def __init__(self, client=None, model: str = MEDIA_MODEL, effort: str = MEDIA_EFFORT,
                 config: MediaConfig = MEDIA):
        if client is None:
            import anthropic  # 매체 수집을 쓸 때만 필요
            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.effort = effort
        self.cfg = config
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "web_searches": 0}

    def collect(self, fixtures: list[dict]) -> list[dict]:
        if not fixtures:
            return []
        ids = [f["id"] for f in fixtures]
        lines = "\n".join(f"- fixture_id {f['id']}: {f['home']} vs {f['away']} (kick-off {f['kickoff']} UTC)"
                          for f in fixtures)
        user = {"role": "user", "content": f"Premier League fixtures:\n{lines}\n\n"
                                           "Find UK media score predictions for these fixtures."}
        tools = [
            {"type": "web_search_20260209", "name": "web_search",
             "max_uses": self.cfg.max_searches_per_call, "allowed_domains": MEDIA_DOMAINS},
            _record_tool(ids),
        ]
        messages = [user]
        for _ in range(MAX_CONTINUATIONS + 1):
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                betas=[FALLBACK_BETA],
                fallbacks="default",
                output_config={"effort": self.effort},
                system=SYSTEM_PROMPT,
                tools=tools,
                messages=messages,
            )
            self._track(response)
            if response.stop_reason == "refusal":
                log.warning("매체 수집 요청이 거절됨: %s", getattr(response, "stop_details", None))
                return []

            calls = [b for b in response.content if b.type == "tool_use" and b.name == "record_predictions"]
            if calls:
                return self._clean(calls[0].input, fixtures)
            if response.stop_reason == "pause_turn":  # 서버 쪽 검색이 길어져 잠시 멈춤 → 이어서 진행
                messages = [user, {"role": "assistant", "content": response.content}]
                continue
            if response.stop_reason == "end_turn":   # 도구를 안 부르고 끝냄 → 한 번 더 요청
                messages = messages + [
                    {"role": "assistant", "content": response.content},
                    {"role": "user", "content": "Call record_predictions now with what you found "
                                                "(an empty list if nothing)."},
                ]
                continue
            log.warning("매체 수집 중단: stop_reason=%s", response.stop_reason)
            return []
        log.warning("매체 수집: 이어서 진행 횟수 초과")
        return []

    def _track(self, response) -> None:
        self.usage["calls"] += 1
        u = getattr(response, "usage", None)
        if u is None:
            return
        self.usage["input_tokens"] += getattr(u, "input_tokens", 0) or 0
        self.usage["output_tokens"] += getattr(u, "output_tokens", 0) or 0
        server = getattr(u, "server_tool_use", None)
        self.usage["web_searches"] += (getattr(server, "web_search_requests", 0) or 0) if server else 0

    @staticmethod
    def _clean(payload: dict, fixtures: list[dict]) -> list[dict]:
        by_id = {f["id"]: f for f in fixtures}
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        out, seen = [], set()
        for p in (payload or {}).get("predictions", []):
            f = by_id.get(str(p.get("fixture_id")))
            hg, ag = p.get("home_goals"), p.get("away_goals")
            url = str(p.get("url", ""))
            if not f or not isinstance(hg, int) or not isinstance(ag, int) or not (0 <= hg <= 12 and 0 <= ag <= 12):
                continue
            if not _allowed(url):  # 허용 매체가 아닌 링크는 버림
                continue
            outlet = str(p.get("outlet", "")).strip() or urlparse(url).hostname
            author = str(p.get("author", "")).strip() or outlet
            key = (f["id"], outlet.lower(), author.lower())
            if key in seen:
                continue
            seen.add(key)
            out.append({"fixture_id": f["id"], "home": f["home"], "away": f["away"], "kickoff": f["kickoff"],
                        "outlet": outlet, "author": author, "home_goals": hg, "away_goals": ag,
                        "url": url, "headline": str(p.get("headline", ""))[:200], "collected_at": now})
        return out
