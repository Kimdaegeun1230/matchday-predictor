"""PredictionAgent: 팀 전력 + 선수 보정 + 폼을 합쳐 Dixon-Coles 포아송 모델로 스코어를 예측한다."""
from __future__ import annotations

import math

from ..config import MODEL, ModelConfig
from ..models import Prediction, SquadReport, TeamStrength
from .base import Agent, MatchContext


def poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * lam ** k / math.factorial(k)


def dixon_coles_tau(h: int, a: int, lam: float, mu: float, rho: float) -> float:
    if h == 0 and a == 0:
        return 1 - lam * mu * rho
    if h == 0 and a == 1:
        return 1 + lam * rho
    if h == 1 and a == 0:
        return 1 + mu * rho
    if h == 1 and a == 1:
        return 1 - rho
    return 1.0


def score_matrix(lam: float, mu: float, rho: float, max_goals: int) -> list[list[float]]:
    ph = [poisson_pmf(i, lam) for i in range(max_goals + 1)]
    pa = [poisson_pmf(j, mu) for j in range(max_goals + 1)]
    mat = [[ph[i] * pa[j] * dixon_coles_tau(i, j, lam, mu, rho) for j in range(max_goals + 1)]
           for i in range(max_goals + 1)]
    total = sum(map(sum, mat))
    return [[v / total for v in row] for row in mat]


class PredictionAgent(Agent):
    name = "predictor"
    role = "기대득점/스코어/승무패 확률 예측"

    def __init__(self, config: ModelConfig = MODEL):
        super().__init__()
        self.cfg = config

    def expected_goals(self, ctx: MatchContext, home_id: int, away_id: int) -> tuple[float, float]:
        hs = ctx.team_strengths.get(home_id, TeamStrength(home_id))
        as_ = ctx.team_strengths.get(away_id, TeamStrength(away_id))
        hr = ctx.squad_reports.get(home_id, SquadReport(home_id))
        ar = ctx.squad_reports.get(away_id, SquadReport(away_id))

        lam = ctx.league_avg_home_goals * hs.attack_home * as_.defense_away * hr.attack_factor * ar.defense_factor
        mu = ctx.league_avg_away_goals * as_.attack_away * hs.defense_home * ar.attack_factor * hr.defense_factor

        form_diff = max(-1.0, min(1.0, (hs.form_ppg - as_.form_ppg) / 3.0))
        lam *= 1 + self.cfg.form_weight * form_diff
        mu *= 1 - self.cfg.form_weight * form_diff
        return max(lam, 0.05), max(mu, 0.05)

    def run(self, ctx: MatchContext) -> str:
        n = self.cfg.max_goals
        for m in ctx.fixtures:
            lam, mu = self.expected_goals(ctx, m.home.id, m.away.id)
            mat = score_matrix(lam, mu, self.cfg.dixon_coles_rho, n)

            cells = [(mat[i][j], i, j) for i in range(n + 1) for j in range(n + 1)]
            home_win = sum(p for p, i, j in cells if i > j)
            draw = sum(p for p, i, j in cells if i == j)
            away_win = sum(p for p, i, j in cells if i < j)
            over25 = sum(p for p, i, j in cells if i + j > 2)
            btts = sum(p for p, i, j in cells if i > 0 and j > 0)

            # 가장 가능성 높은 결과(승/무/패) 안에서 최빈 스코어를 대표 예측으로 사용
            outcome = max((home_win, "H"), (draw, "D"), (away_win, "A"))[1]
            in_outcome = [c for c in cells if (c[1] > c[2], c[1] == c[2], c[1] < c[2])["HDA".index(outcome)]]
            _, ph, pa = max(in_outcome)
            top = sorted(cells, reverse=True)[:5]

            hs = ctx.team_strengths.get(m.home.id, TeamStrength(m.home.id))
            as_ = ctx.team_strengths.get(m.away.id, TeamStrength(m.away.id))
            notes = self._notes(ctx, m.home.id, m.away.id, hs, as_)

            ctx.predictions.append(Prediction(
                match_id=m.id,
                league=ctx.league.key,
                kickoff=m.utc_date.isoformat(),
                matchday=m.matchday,
                home_team=m.home.name,
                away_team=m.away.name,
                home_tla=m.home.tla or m.home.name[:3].upper(),
                away_tla=m.away.tla or m.away.name[:3].upper(),
                status=m.status,
                actual_score=f"{m.home_goals}-{m.away_goals}" if m.finished else None,
                home_xg=round(lam, 2),
                away_xg=round(mu, 2),
                predicted_score=f"{ph}-{pa}",
                home_win=round(home_win, 4),
                draw=round(draw, 4),
                away_win=round(away_win, 4),
                over_2_5=round(over25, 4),
                btts=round(btts, 4),
                top_scores=[{"score": f"{i}-{j}", "prob": round(p, 4)} for p, i, j in top],
                confidence=self._confidence(max(home_win, draw, away_win), min(hs.games, as_.games)),
                score_grid=[[round(mat[i][j], 4) for j in range(6)] for i in range(6)],
                home_squad=self._squad(ctx, m.home.id),
                away_squad=self._squad(ctx, m.away.id),
                sources=m.sources,
                confirmed_by=m.confirmed_by,
                notes=notes,
            ))
        return f"{len(ctx.predictions)}경기 예측 완료"

    @staticmethod
    def _confidence(top_prob: float, min_games: int) -> str:
        if min_games < 4:
            return "low"
        if top_prob >= 0.6:
            return "high"
        if top_prob >= 0.45:
            return "medium"
        return "low"

    @staticmethod
    def _squad(ctx: MatchContext, team_id: int) -> dict:
        rep = ctx.squad_reports.get(team_id, SquadReport(team_id))
        st = ctx.team_strengths.get(team_id, TeamStrength(team_id))
        return {
            "attack_factor": round(rep.attack_factor, 3),
            "defense_factor": round(rep.defense_factor, 3),
            "form": st.form,
            "form_ppg": round(st.form_ppg, 2),
            "missing": rep.missing_players,
            "key": rep.key_players,
            "has_players": bool(ctx.players.get(team_id)),
        }

    @staticmethod
    def _notes(ctx: MatchContext, home_id: int, away_id: int, hs: TeamStrength, as_: TeamStrength) -> list[str]:
        notes = []
        for tid, label in ((home_id, "홈"), (away_id, "원정")):
            rep = ctx.squad_reports.get(tid)
            if rep and rep.missing_players:
                notes.append(f"{label} 결장: " + ", ".join(
                    f"{p['name']} ({p['play_prob']:.0%}, {p['reason']})" for p in rep.missing_players))
            if rep and rep.key_players:
                notes.append(f"{label} 핵심: " + ", ".join(
                    f"{p['name']} {p['goals']}G {p['assists']}A" for p in rep.key_players))
        notes.append(f"최근 폼(경기당 승점) 홈 {hs.form_ppg:.2f} / 원정 {as_.form_ppg:.2f}")
        return notes
