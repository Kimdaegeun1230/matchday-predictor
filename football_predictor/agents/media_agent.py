"""MediaConsensusAgent: 영국 현지 매체의 스코어 예측을 적중률로 가중해 모델 예측에 일부 반영한다.

1. 끝난 경기와 비교해 칼럼니스트별 적중률 → 가중치 (적중률 50% = 1.0, 높을수록 커짐)
2. 경기마다 매체 예측을 가중 투표해 승/무/패 합의 확률을 만든다
3. 반영 비율 = 최대 30% × 가중치합 / (가중치합 + 3) — 예측이 많고 잘 맞히는 사람일수록 크게
4. 스코어 확률표의 승/무/패 영역을 새 확률에 맞게 다시 맞춰, 대표 스코어·오버·양팀득점도 함께 갱신

매체 예측 파일(data/media/media.json)이 없으면 아무것도 바꾸지 않는다.
"""
from __future__ import annotations

from pathlib import Path

from ..config import MEDIA, MEDIA_FILE, MediaConfig
from ..media.store import MediaStore, outcome, pundit_key
from .base import Agent, MatchContext
from .prediction_agent import confidence, outcome_of, summarize, xg_score


class MediaConsensusAgent(Agent):
    name = "media_consensus"
    role = "현지 매체 예측 합의 반영"

    def __init__(self, path: Path = MEDIA_FILE, config: MediaConfig = MEDIA):
        super().__init__()
        self.path = path
        self.cfg = config

    def run(self, ctx: MatchContext) -> str:
        if not self.path.exists():
            ctx.media_report = {"available": False}
            return "매체 예측 데이터 없음 (반영 안 함)"
        store = MediaStore(self.path, self.cfg)
        # 적중률은 기준일 이전에 끝난 경기로만 계산 (그날 결과를 미리 아는 일이 없게)
        results = {str(m.id): (m.home_goals, m.away_goals) for m in ctx.finished_matches}
        stats = store.pundit_stats(results)

        adjusted = 0
        for p in ctx.predictions:
            items = store.for_fixture(str(p.match_id))
            if not items:
                continue
            model_probs = {"H": p.home_win, "D": p.draw, "A": p.away_win}
            votes = {"H": 0.0, "D": 0.0, "A": 0.0}
            listed = []
            for it in items:
                s = stats.get(pundit_key(it))
                w = s.weight(self.cfg) if s else 1.0
                votes[outcome(it["home_goals"], it["away_goals"])] += w
                listed.append({"outlet": it["outlet"], "author": it["author"],
                               "score": f"{it['home_goals']}-{it['away_goals']}", "url": it["url"],
                               "headline": it["headline"], "weight": round(w, 2),
                               "hit_rate": round(s.hit_rate(self.cfg), 3) if s else None,
                               "games": s.games if s else 0})
            total_w = sum(votes.values())
            consensus = {k: v / total_w for k, v in votes.items()}
            blend = self.cfg.max_blend * total_w / (total_w + self.cfg.blend_half_weight)
            final = {k: (1 - blend) * model_probs[k] + blend * consensus[k] for k in "HDA"}

            mat, min_games = ctx.score_matrices[p.match_id]
            new = self._rescale(mat, model_probs, final)
            s2 = summarize(new)
            for k, v in s2.items():
                setattr(p, k, v)
            p.xg_score = xg_score(p.home_xg, p.away_xg, outcome_of(s2))
            p.confidence = confidence(max(s2["home_win"], s2["draw"], s2["away_win"]), min_games)
            p.media = {
                "count": len(items),
                "blend": round(blend, 3),
                "consensus": {k: round(v, 3) for k, v in consensus.items()},
                "model_only": {k: round(v, 4) for k, v in model_probs.items()},
                "items": sorted(listed, key=lambda x: -x["weight"]),
            }
            p.notes.append(f"현지 매체 예측 {len(items)}건 반영 ({blend:.0%})")
            adjusted += 1

        leaderboard = sorted((s.to_dict(self.cfg) for s in stats.values() if s.games),
                             key=lambda d: (-d["hit_rate"], -d["games"]))
        ctx.media_report = {"available": True, "predictions": len(store.predictions),
                            "pundits": len(stats), "leaderboard": leaderboard[:15]}
        return f"매체 예측 {len(store.predictions)}건·칼럼니스트 {len(stats)}명, {adjusted}경기에 반영"

    @staticmethod
    def _rescale(mat, before: dict, after: dict):
        """승/무/패 영역별로 비율을 곱해 합계가 새 확률이 되게 한다 (영역 안의 스코어 분포 모양은 유지)."""
        n = len(mat)
        out = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                k = "H" if i > j else "D" if i == j else "A"
                out[i][j] = mat[i][j] * (after[k] / before[k] if before[k] > 0 else 0)
        total = sum(map(sum, out))
        return [[v / total for v in row] for row in out]
