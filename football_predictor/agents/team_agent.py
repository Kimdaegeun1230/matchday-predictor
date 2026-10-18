"""TeamAnalystAgent: 경기 결과로 팀별 홈/원정 공격력·수비력과 최근 폼을 추정한다.

방법: 시간 가중(최근 경기일수록 큰 가중치) + 상대 전력 보정 반복 추정(Maher 방식).
표본이 적은 팀은 shrinkage로 리그 평균(1.0) 쪽으로 당긴다.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone

from ..config import MODEL, ModelConfig
from ..models import TeamStrength
from .base import Agent, MatchContext


class TeamAnalystAgent(Agent):
    name = "team_analyst"
    role = "팀 공격/수비 전력 및 폼 분석"

    def __init__(self, config: ModelConfig = MODEL, iterations: int = 25):
        super().__init__()
        self.cfg = config
        self.iterations = iterations

    def run(self, ctx: MatchContext) -> str:
        cutoff = datetime.combine(ctx.target_date, datetime.min.time(), tzinfo=timezone.utc)
        games = [(m, math.exp(-self.cfg.time_decay_xi * (cutoff - m.utc_date).days)) for m in ctx.finished_matches]

        total_w = sum(w for _, w in games)
        if total_w > 0:
            ctx.league_avg_home_goals = sum(m.home_goals * w for m, w in games) / total_w
            ctx.league_avg_away_goals = sum(m.away_goals * w for m, w in games) / total_w
        avg_h, avg_a, k = ctx.league_avg_home_goals, ctx.league_avg_away_goals, self.cfg.shrinkage_games

        team_ids = set(ctx.teams)
        att_h = dict.fromkeys(team_ids, 1.0)
        def_h = dict.fromkeys(team_ids, 1.0)
        att_a = dict.fromkeys(team_ids, 1.0)
        def_a = dict.fromkeys(team_ids, 1.0)

        # 표본이 적을 때 당겨질 사전값: 일반 팀은 리그 평균(1.0), 승격팀은 약하게
        prior_att = {t: self.cfg.promoted_attack_prior if ctx.teams[t].promoted else 1.0 for t in team_ids}
        prior_def = {t: self.cfg.promoted_defense_prior if ctx.teams[t].promoted else 1.0 for t in team_ids}

        for _ in range(self.iterations):
            num = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])  # att_h, def_h, att_a, def_a
            den = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
            for m, w in games:
                h, a = m.home.id, m.away.id
                num[h][0] += w * m.home_goals; den[h][0] += w * avg_h * def_a[a]
                num[h][1] += w * m.away_goals; den[h][1] += w * avg_a * att_a[a]
                num[a][2] += w * m.away_goals; den[a][2] += w * avg_a * def_h[h]
                num[a][3] += w * m.home_goals; den[a][3] += w * avg_h * att_h[h]
            for t in team_ids:
                n, d = num[t], den[t]
                pa, pd = prior_att[t], prior_def[t]
                att_h[t] = (n[0] + k * avg_h * pa) / (d[0] + k * avg_h)
                def_h[t] = (n[1] + k * avg_a * pd) / (d[1] + k * avg_a)
                att_a[t] = (n[2] + k * avg_a * pa) / (d[2] + k * avg_a)
                def_a[t] = (n[3] + k * avg_h * pd) / (d[3] + k * avg_h)

        recent = self._form(ctx)
        ctx.team_strengths = {
            t: TeamStrength(t, att_h[t], def_h[t], att_a[t], def_a[t],
                            form_ppg=recent[t][0], form=recent[t][1], games=recent[t][2])
            for t in team_ids
        }
        ctx.standings = self._standings(ctx)

        top = sorted(ctx.team_strengths.values(), key=lambda s: s.rating, reverse=True)[:3]
        names = ", ".join(ctx.teams[s.team_id].short_name or ctx.teams[s.team_id].name for s in top)
        return f"리그 평균 홈 {avg_h:.2f} / 원정 {avg_a:.2f} 골, 전력 상위: {names}"

    def _form(self, ctx: MatchContext) -> dict[int, tuple[float, str, int]]:
        """팀별 (최근 N경기 평균 승점, 최근 N경기 결과 문자열, 전체 경기 수)."""
        history: dict[int, list[str]] = defaultdict(list)
        for m in sorted(ctx.finished_matches, key=lambda m: m.utc_date):
            diff = m.home_goals - m.away_goals
            history[m.home.id].append("W" if diff > 0 else "D" if diff == 0 else "L")
            history[m.away.id].append("L" if diff > 0 else "D" if diff == 0 else "W")
        points = {"W": 3, "D": 1, "L": 0}
        out = {}
        for t in ctx.teams:
            results = history.get(t, [])
            window = results[-self.cfg.form_window:]
            ppg = sum(points[r] for r in window) / len(window) if window else 1.35
            out[t] = (ppg, "".join(window), len(results))
        return out

    @staticmethod
    def _standings(ctx: MatchContext) -> list[dict]:
        """이번 시즌(7월 1일 이후) 완료 경기로 만든 순위표."""
        season_year = ctx.target_date.year if ctx.target_date.month >= 7 else ctx.target_date.year - 1
        season_start = datetime(season_year, 7, 1, tzinfo=timezone.utc)
        rows: dict[int, dict] = {}
        for m in ctx.finished_matches:
            if m.utc_date < season_start:
                continue
            for tid, gf, ga in ((m.home.id, m.home_goals, m.away_goals), (m.away.id, m.away_goals, m.home_goals)):
                r = rows.setdefault(tid, {"team_id": tid, "played": 0, "won": 0, "drawn": 0, "lost": 0,
                                          "gf": 0, "ga": 0, "points": 0})
                r["played"] += 1
                r["gf"] += gf
                r["ga"] += ga
                if gf > ga:
                    r["won"] += 1; r["points"] += 3
                elif gf == ga:
                    r["drawn"] += 1; r["points"] += 1
                else:
                    r["lost"] += 1
        # 이번 시즌 아직 경기가 없는 팀도 포함 (직전 시즌에만 있던 강등팀 제외)
        for tid, team in ctx.teams.items():
            if team.current:
                rows.setdefault(tid, {"team_id": tid, "played": 0, "won": 0, "drawn": 0, "lost": 0,
                                      "gf": 0, "ga": 0, "points": 0})
        table = sorted(rows.values(), key=lambda r: (r["points"], r["gf"] - r["ga"], r["gf"]), reverse=True)
        for i, r in enumerate(table, 1):
            r["position"] = i
        return table
