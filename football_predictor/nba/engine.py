"""NBA 예측 에이전트 팀.

    NbaData ─▶ NbaQuality ─▶ NbaTeam ─▶ NbaInjury ─▶ NbaPredictor ─▶ NbaMedia ─▶ (리포트)

모델 (축구와 다름: 100점대 점수, 무승부 없음)
- 득점 = 리그 평균 + 공격력(팀) + 수비 허용(상대) ± 홈 이점/2  — 시간 가중·상대 보정 반복 추정(가법 모델)
- 점수차 ~ 정규분포(평균 = 기대 점수차, 표준편차 = 잔차로 추정) → 승리 확률
- 보정: 전날 경기(백투백) 팀 감점, 결장 주전의 평균 득점 일부 감점, 현지 매체 예측(적중률 가중) 일부 반영
"""
from __future__ import annotations

import json
import logging
import math
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..agents.base import Agent
from ..config import DATA_DIR, MEDIA, NBA, League, MediaConfig, NbaConfig
from ..media.store import MediaStore, pundit_key
from ..providers.http import ApiError
from .sources import BalldontlieSource, EspnInjurySource, NbaData, NbaGame, NbaInjury, collect, season_of

log = logging.getLogger("agent.nba")
NBA_MEDIA_FILE = DATA_DIR / "media" / "nba_media.json"
NBA_LINES_FILE = DATA_DIR / "media" / "nba_lines.json"   # 경기 전날 밤 확정한 언더/오버 기준점


def ou_line(total: float) -> float:
    """기준점은 x.5 로 (동점 없음)."""
    return math.floor(total) + 0.5


def load_lines(path: Path = NBA_LINES_FILE) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


@dataclass
class NbaContext:
    league: League
    target_date: date
    data: NbaData | None = None
    history: list[NbaGame] = field(default_factory=list)
    fixtures: list[NbaGame] = field(default_factory=list)
    mu: float = 113.0
    hca: float = 2.5
    sigma: float = 12.5
    score_sd: float = 12.5
    total_sd: float = 18.0
    off: dict[str, float] = field(default_factory=dict)
    dfn: dict[str, float] = field(default_factory=dict)
    standings: list[dict] = field(default_factory=list)
    injury_loss: dict[str, float] = field(default_factory=dict)
    missing: dict[str, list[dict]] = field(default_factory=dict)
    predictions: list[dict] = field(default_factory=list)
    data_quality: dict = field(default_factory=dict)
    media_report: dict = field(default_factory=dict)
    log: list[dict] = field(default_factory=list)


class NbaDataAgent(Agent):
    name = "nba_data"
    role = "경기 일정·결과·부상 수집"

    def __init__(self, engine: "NbaEngine"):
        super().__init__()
        self.engine = engine

    def run(self, ctx: NbaContext) -> str:
        ctx.data = self.engine.data()
        cutoff = ctx.target_date
        ctx.history = [g for g in ctx.data.games if g.finished and g.date < cutoff]
        ctx.fixtures = sorted((g for g in ctx.data.games if g.date == cutoff),
                              key=lambda g: (g.tipoff or datetime.max.replace(tzinfo=timezone.utc), g.id))
        ok = [s["name"] for s in ctx.data.status if s["ok"]]
        return f"[{' + '.join(ok)}] 학습 경기 {len(ctx.history)}, {cutoff} 경기 {len(ctx.fixtures)}, 부상 {len(ctx.data.injuries)}명"


class NbaQualityAgent(Agent):
    name = "nba_quality"
    role = "출처 상태·결과 반영 지연·부상 정보 점검"

    def run(self, ctx: NbaContext) -> str:
        checks = []
        st = ctx.data.status
        ok = [s["name"] for s in st if s["ok"]]
        checks.append({"label": "데이터 출처", "status": "good" if len(ok) == len(st) else "warn" if ok else "bad",
                       "detail": f"응답 {len(ok)}/{len(st)}: {', '.join(ok)}" + "".join(
                           f" · {s['name']}: {s['error']}" for s in st if s["error"])})
        now = date.today()
        horizon = min(now, ctx.target_date)
        stale = [g for g in ctx.data.games if g.date < horizon - timedelta(days=0) and not g.finished
                 and g.date >= horizon - timedelta(days=10)]
        checks.append({"label": "결과 반영 지연", "status": "good" if not stale else "warn",
                       "detail": "지난 경기 결과가 모두 반영됨" if not stale else f"지난 경기 {len(stale)}개 결과 없음"})
        inj_ok = any(s["key"] == "espn" and s["ok"] for s in st)
        checks.append({"label": "부상·결장 정보", "status": "good" if inj_ok else "warn",
                       "detail": "출처: ESPN (비공식 출처 — 공식 발표와 다를 수 있음)" if inj_ok else "부상 정보 없음"})
        levels = [c["status"] for c in checks]
        overall = "bad" if "bad" in levels else "warn" if "warn" in levels else "good"
        ctx.data_quality = {"overall": overall, "checks": checks, "conflicts": [], "injury_data": inj_ok}
        return f"데이터 품질 {dict(good='양호', warn='주의', bad='부족')[overall]}"


class NbaTeamAgent(Agent):
    name = "nba_team"
    role = "팀 공격·수비 점수 능력, 홈 이점, 점수차 분산 추정"

    def __init__(self, config: NbaConfig = NBA):
        super().__init__()
        self.cfg = config

    def run(self, ctx: NbaContext) -> str:
        cfg = self.cfg
        teams = set(ctx.data.teams) or {g.home for g in ctx.data.games} | {g.away for g in ctx.data.games}
        games = [(g, math.exp(-cfg.time_decay_xi * (ctx.target_date - g.date).days)) for g in ctx.history]
        W = sum(w for _, w in games)
        if W:
            ctx.mu = sum(w * (g.home_pts + g.away_pts) / 2 for g, w in games) / W
            ctx.hca = sum(w * (g.home_pts - g.away_pts) for g, w in games) / W
        off = dict.fromkeys(teams, 0.0)
        dfn = dict.fromkeys(teams, 0.0)
        k = cfg.shrinkage_games
        for _ in range(cfg.iterations):
            num_o, num_d, den = defaultdict(float), defaultdict(float), defaultdict(float)
            for g, w in games:
                h, a = g.home, g.away
                exp_h = ctx.mu + ctx.hca / 2
                exp_a = ctx.mu - ctx.hca / 2
                num_o[h] += w * (g.home_pts - exp_h - dfn.get(a, 0)); num_o[a] += w * (g.away_pts - exp_a - dfn.get(h, 0))
                num_d[a] += w * (g.home_pts - exp_h - off.get(h, 0)); num_d[h] += w * (g.away_pts - exp_a - off.get(a, 0))
                den[h] += w; den[a] += w
            off = {t: num_o[t] / (den[t] + k) for t in teams}
            dfn = {t: num_d[t] / (den[t] + k) for t in teams}
        ctx.off, ctx.dfn = off, dfn
        # 점수차 표준편차: 모델 잔차로 추정 (표본 적으면 기본값)
        res = [(g.home_pts - g.away_pts) - (ctx.hca + off.get(g.home, 0) + dfn.get(g.away, 0)
                                            - off.get(g.away, 0) - dfn.get(g.home, 0)) for g, _ in games[-600:]]
        ctx.sigma = math.sqrt(sum(r * r for r in res) / len(res)) if len(res) > 50 else cfg.margin_sd_default
        # 팀 한 쪽 득점의 표준편차 (기대 득점이 110 근처에 몰려도 실제 점수는 이만큼 흩어짐)
        tres = []
        for g, _ in games[-600:]:
            tres.append(g.home_pts - (ctx.mu + ctx.hca / 2 + off.get(g.home, 0) + dfn.get(g.away, 0)))
            tres.append(g.away_pts - (ctx.mu - ctx.hca / 2 + off.get(g.away, 0) + dfn.get(g.home, 0)))
        ctx.score_sd = math.sqrt(sum(r * r for r in tres) / len(tres)) if len(tres) > 100 else cfg.margin_sd_default
        tot = [tres[i] + tres[i + 1] for i in range(0, len(tres) - 1, 2)]  # 두 팀 득점 합의 잔차
        ctx.total_sd = math.sqrt(sum(r * r for r in tot) / len(tot)) if len(tot) > 50 else 18.0
        ctx.standings = self._standings(ctx)
        top = sorted(teams, key=lambda t: off[t] - dfn[t], reverse=True)[:3]
        names = ", ".join(ctx.data.teams[t].name if t in ctx.data.teams else t for t in top)
        return f"리그 평균 {ctx.mu:.1f}점, 홈 이점 {ctx.hca:+.1f}, 점수차 표준편차 {ctx.sigma:.1f}, 전력 상위: {names}"

    @staticmethod
    def _standings(ctx: NbaContext) -> list[dict]:
        season = season_of(ctx.target_date)
        rows: dict[str, dict] = {}
        for t, team in ctx.data.teams.items():
            rows[t] = {"team": t, "name": team.name, "tla": team.abbr, "conference": team.conference,
                       "played": 0, "won": 0, "lost": 0, "pf": 0, "pa": 0, "form": ""}
        for g in sorted(ctx.history, key=lambda g: g.date):
            if g.season != season or g.postseason:
                continue
            for t, pf, pa in ((g.home, g.home_pts, g.away_pts), (g.away, g.away_pts, g.home_pts)):
                r = rows.setdefault(t, {"team": t, "name": t, "tla": t[:3].upper(), "conference": "", "played": 0,
                                        "won": 0, "lost": 0, "pf": 0, "pa": 0, "form": ""})
                r["played"] += 1; r["pf"] += pf; r["pa"] += pa
                win = pf > pa
                r["won" if win else "lost"] += 1
                r["form"] = (r["form"] + ("W" if win else "L"))[-10:]
        out = []
        for r in rows.values():
            n = r["played"] or 1
            r.update(win_pct=round(r["won"] / n, 3) if r["played"] else 0.0, ppg=round(r["pf"] / n, 1),
                     oppg=round(r["pa"] / n, 1), net=round((r["pf"] - r["pa"]) / n, 1),
                     rating=round(ctx.off.get(r["team"], 0) - ctx.dfn.get(r["team"], 0), 1),
                     off=round(ctx.off.get(r["team"], 0), 1), dfn=round(ctx.dfn.get(r["team"], 0), 1))
            out.append(r)
        out.sort(key=lambda r: (r["win_pct"], r["net"]), reverse=True)
        for i, r in enumerate(out, 1):
            r["position"] = i
        return out


class NbaInjuryAgent(Agent):
    name = "nba_injury"
    role = "결장 주전의 평균 득점으로 득점 감소 추정"

    def __init__(self, config: NbaConfig = NBA):
        super().__init__()
        self.cfg = config

    def run(self, ctx: NbaContext) -> str:
        teams = {g.home for g in ctx.fixtures} | {g.away for g in ctx.fixtures}
        # 부상 정보는 '지금' 기준이라 오늘 근처 경기에만 적용 (지난 경기 예측에 현재 부상을 쓰지 않음)
        if not 0 <= (ctx.target_date - date.today()).days <= self.cfg.injury_window_days:
            return "오늘 근처 경기가 아니라 부상 정보 미적용"
        team_games = defaultdict(int)
        season = season_of(ctx.target_date)
        for g in ctx.history:
            if g.season == season:
                team_games[g.home] += 1; team_games[g.away] += 1
        total = 0
        for inj in ctx.data.injuries:
            if inj.team not in teams or inj.play_prob >= 1:
                continue
            regular = inj.games_played >= self.cfg.injury_regular_share * max(team_games[inj.team], 1)
            loss = inj.ppg * (1 - inj.play_prob) * self.cfg.injury_replacement if regular else 0.0
            ctx.injury_loss[inj.team] = ctx.injury_loss.get(inj.team, 0.0) + loss
            ctx.missing.setdefault(inj.team, []).append({
                "name": inj.player, "status": inj.status, "play_prob": inj.play_prob, "ppg": round(inj.ppg, 1),
                "mpg": round(inj.mpg, 1), "reason": inj.detail, "counted": regular, "loss": round(loss, 1)})
            total += 1
        # 결장 손실을 그냥 더하면 주전 여럿이 빠진 팀이 50점씩 깎임 → 상한 쪽으로 완만하게 포화
        cap = self.cfg.injury_cap
        for t, raw in ctx.injury_loss.items():
            if raw > 0:
                scale = cap * (1 - math.exp(-raw / cap)) / raw
                ctx.injury_loss[t] = raw * scale
                for m in ctx.missing.get(t, []):
                    m["loss"] = round(m["loss"] * scale, 1)
        for t in ctx.missing:
            ctx.missing[t].sort(key=lambda m: -m["loss"])
        return f"경기 팀 {len(teams)}곳, 결장·불확실 {total}명 반영"


def predicted_score(eh: float, ea: float) -> str:
    h, a = round(eh), round(ea)
    if eh - ea > 0 and h <= a:
        h = a + 1
    if eh - ea < 0 and a <= h:
        a = h + 1
    if h == a:  # 동점 없음 (연장)
        h += 1 if eh >= ea else 0
        a += 0 if eh >= ea else 1
    return f"{h}-{a}"


def margin_buckets(margin: float, sigma: float) -> list[dict]:
    """점수차 구간별 확률 (홈 기준 +, 원정 기준 −)."""
    edges = [(-math.inf, -15.5, "원정 16+"), (-15.5, -10.5, "원정 11–15"), (-10.5, -5.5, "원정 6–10"),
             (-5.5, 0, "원정 1–5"), (0, 5.5, "홈 1–5"), (5.5, 10.5, "홈 6–10"), (10.5, 15.5, "홈 11–15"),
             (15.5, math.inf, "홈 16+")]
    return [{"label": lab, "prob": round(phi((hi - margin) / sigma) - phi((lo - margin) / sigma), 4)}
            for lo, hi, lab in edges]


class NbaPredictorAgent(Agent):
    name = "nba_predictor"
    role = "기대 득점·점수차 분포·승리 확률 예측"

    def __init__(self, config: NbaConfig = NBA):
        super().__init__()
        self.cfg = config

    def run(self, ctx: NbaContext) -> str:
        played_dates = defaultdict(set)
        for g in ctx.data.games:
            played_dates[g.home].add(g.date); played_dates[g.away].add(g.date)
        min_games = defaultdict(int)
        season = season_of(ctx.target_date)
        for g in ctx.history:
            if g.season == season:
                min_games[g.home] += 1; min_games[g.away] += 1
        for g in ctx.fixtures:
            h, a = g.home, g.away
            eh = ctx.mu + ctx.hca / 2 + ctx.off.get(h, 0) + ctx.dfn.get(a, 0) - ctx.injury_loss.get(h, 0)
            ea = ctx.mu - ctx.hca / 2 + ctx.off.get(a, 0) + ctx.dfn.get(h, 0) - ctx.injury_loss.get(a, 0)
            notes = []
            for t, label in ((h, "홈"), (a, "원정")):
                if (g.date - timedelta(days=1)) in played_dates[t]:
                    if t == h:
                        eh -= self.cfg.back_to_back_penalty
                    else:
                        ea -= self.cfg.back_to_back_penalty
                    notes.append(f"{label} 팀 백투백(전날 경기) −{self.cfg.back_to_back_penalty}점")
            ctx.predictions.append(self._prediction(ctx, g, eh, ea, notes, min(min_games[h], min_games[a])))
        return f"{len(ctx.predictions)}경기 예측 완료"

    def _prediction(self, ctx, g: NbaGame, eh: float, ea: float, notes: list[str], min_games: int) -> dict:
        teams = ctx.data.teams
        margin = eh - ea
        p_home = phi(margin / ctx.sigma)
        top = max(p_home, 1 - p_home)
        conf = "low" if min_games < 5 else "high" if top >= 0.72 else "medium" if top >= 0.6 else "low"

        def squad(t):
            return {"missing": ctx.missing.get(t, []), "points_lost": round(ctx.injury_loss.get(t, 0), 1),
                    "has_players": True, "form": next((r["form"] for r in ctx.standings if r["team"] == t), ""),
                    "record": next((f"{r['won']}-{r['lost']}" for r in ctx.standings if r["team"] == t), "")}
        return {
            "sport": "nba", "match_id": g.id, "league": ctx.league.key,
            "kickoff": (g.tipoff or datetime.combine(g.date, datetime.min.time(), tzinfo=timezone.utc)).isoformat(),
            "matchday": None, "date": g.date.isoformat(),
            "home_team": teams[g.home].name if g.home in teams else g.home,
            "away_team": teams[g.away].name if g.away in teams else g.away,
            "home_tla": teams[g.home].abbr if g.home in teams else g.home[:3].upper(),
            "away_tla": teams[g.away].abbr if g.away in teams else g.away[:3].upper(),
            "status": g.status, "actual_score": f"{g.home_pts}-{g.away_pts}" if g.finished else None,
            "home_xg": round(eh, 1), "away_xg": round(ea, 1),
            "predicted_score": predicted_score(eh, ea),
            "home_win": round(p_home, 4), "draw": 0.0, "away_win": round(1 - p_home, 4),
            "margin": round(margin, 1), "total": round(eh + ea, 1), "sigma": round(ctx.sigma, 1),
            "score_sd": round(ctx.score_sd, 1),
            "margin_buckets": margin_buckets(margin, ctx.sigma),
            "confidence": conf, "home_squad": squad(g.home), "away_squad": squad(g.away),
            "sources": [s["name"] for s in ctx.data.status if s["ok"] and s["key"] == "balldontlie"],
            "confirmed_by": [], "media": {}, "notes": notes,
        }


class NbaMediaAgent(Agent):
    name = "nba_media"
    role = "미국 현지 매체 예측 합의 반영"

    def __init__(self, path: Path = NBA_MEDIA_FILE, config: MediaConfig = MEDIA):
        super().__init__()
        self.path = path
        self.cfg = config

    def run(self, ctx: NbaContext) -> str:
        if not self.path.exists():
            ctx.media_report = {"available": False}
            return "매체 예측 데이터 없음 (반영 안 함)"
        store = MediaStore(self.path, self.cfg)
        results = {str(g.id): (g.home_pts, g.away_pts) for g in ctx.history}
        stats = store.pundit_stats(results)  # 승패 적중 = outcome (무승부 없음)
        adjusted = 0
        for p in ctx.predictions:
            items = store.for_fixture(str(p["match_id"]))
            if not items:
                continue
            wsum, wmargin, listed = 0.0, 0.0, []
            for it in items:
                s = stats.get(pundit_key(it))
                w = s.weight(self.cfg) if s else 1.0
                wsum += w
                wmargin += w * (it["home_goals"] - it["away_goals"])
                listed.append({"outlet": it["outlet"], "author": it["author"],
                               "score": f"{it['home_goals']}-{it['away_goals']}", "url": it["url"],
                               "headline": it["headline"], "weight": round(w, 2),
                               "hit_rate": round(s.hit_rate(self.cfg), 3) if s else None, "games": s.games if s else 0})
            media_margin = wmargin / wsum
            blend = self.cfg.max_blend * wsum / (wsum + self.cfg.blend_half_weight)
            model_home = p["home_win"]
            margin = (1 - blend) * p["margin"] + blend * media_margin
            shift = margin - p["margin"]
            eh, ea = p["home_xg"] + shift / 2, p["away_xg"] - shift / 2
            p_home = phi(margin / p["sigma"])
            p.update(home_xg=round(eh, 1), away_xg=round(ea, 1), margin=round(margin, 1),
                     predicted_score=predicted_score(eh, ea), home_win=round(p_home, 4), away_win=round(1 - p_home, 4),
                     margin_buckets=margin_buckets(margin, p["sigma"]))
            p["media"] = {"count": len(items), "blend": round(blend, 3), "media_margin": round(media_margin, 1),
                          "model_only": {"H": model_home, "D": 0.0, "A": round(1 - model_home, 4)},
                          "consensus": {"H": round(sum(1 for i in items if i["home_goals"] > i["away_goals"]) / len(items), 3),
                                        "D": 0.0,
                                        "A": round(sum(1 for i in items if i["home_goals"] < i["away_goals"]) / len(items), 3)},
                          "items": sorted(listed, key=lambda x: -x["weight"])}
            p["notes"].append(f"현지 매체 예측 {len(items)}건 반영 ({blend:.0%})")
            adjusted += 1
        board = sorted((s.to_dict(self.cfg) for s in stats.values() if s.games), key=lambda d: (-d["hit_rate"], -d["games"]))
        ctx.media_report = {"available": True, "predictions": len(store.predictions), "pundits": len(stats),
                            "leaderboard": board[:15]}
        return f"매체 예측 {len(store.predictions)}건, {adjusted}경기에 반영"


class NbaTotalsAgent(Agent):
    """언더/오버: 기준점(전날 밤 확정, 없으면 현재 예상 총점으로 임시) 대비 오버 확률.
    예상 총점 = 모델 총점과 매체 기사 예상 총점(적중률 가중)을 섞은 값."""
    name = "nba_totals"
    role = "언더/오버 기준점과 오버·언더 확률"

    def __init__(self, media_path: Path = NBA_MEDIA_FILE, lines_path: Path = NBA_LINES_FILE,
                 config: MediaConfig = MEDIA):
        super().__init__()
        self.media_path, self.lines_path, self.cfg = media_path, lines_path, config

    def run(self, ctx: NbaContext) -> str:
        lines = load_lines(self.lines_path)
        store = MediaStore(self.media_path, self.cfg) if self.media_path.exists() else None
        stats = store.pundit_stats({str(g.id): (g.home_pts, g.away_pts) for g in ctx.history}) if store else {}
        locked = with_media = 0
        for p in ctx.predictions:
            fixed = lines.get(str(p["match_id"]))
            # 확정 당시 모델 총점을 기준으로 (이후 부상 소식으로 바뀐 만큼은 현재 총점에 반영됨)
            model_total = p["total"]
            line = fixed["line"] if fixed else ou_line(model_total)
            items = store.for_fixture(str(p["match_id"])) if store else []
            wsum = wtot = 0.0
            over_votes = under_votes = 0
            listed = []
            for it in items:
                s = stats.get(pundit_key(it))
                w = s.weight(self.cfg) if s else 1.0
                t = it["home_goals"] + it["away_goals"]
                wsum += w
                wtot += w * t
                over_votes += t > line
                under_votes += t < line
                listed.append({"outlet": it["outlet"], "author": it["author"], "total": t, "url": it["url"],
                               "pick": "over" if t > line else "under" if t < line else "push"})
            expected, media_total, blend = model_total, None, 0.0
            if wsum:
                media_total = wtot / wsum
                blend = self.cfg.max_blend * wsum / (wsum + self.cfg.blend_half_weight)
                expected = (1 - blend) * model_total + blend * media_total
                with_media += 1
            p_over = 1 - phi((line - expected) / ctx.total_sd)
            actual = None
            if p.get("actual_score"):
                h, a = p["actual_score"].split("-")
                actual = int(h) + int(a)
            p["totals"] = {
                "line": line, "locked": bool(fixed), "set_at": fixed.get("set_at") if fixed else None,
                "model_total": round(model_total, 1), "media_total": round(media_total, 1) if media_total else None,
                "media_count": len(items), "blend": round(blend, 3), "over_votes": over_votes,
                "under_votes": under_votes, "items": listed, "expected_total": round(expected, 1),
                "total_sd": round(ctx.total_sd, 1), "over": round(p_over, 4), "under": round(1 - p_over, 4),
                "pick": "over" if p_over >= 0.5 else "under", "actual_total": actual,
                "result": None if actual is None else ("over" if actual > line else "under"),
            }
            locked += bool(fixed)
        return f"기준점 확정 {locked}경기 · 매체 총점 반영 {with_media}경기 (총점 표준편차 {ctx.total_sd:.1f})"


class NbaEngine:
    """CoordinatorAgent 가 NBA 리그일 때 쓰는 파이프라인. 출처 수집은 한 번 해 두고 날짜마다 재사용한다."""

    def __init__(self, league: League, games_source=None, injury_source=None, media_path: Path = NBA_MEDIA_FILE,
                 lines_path: Path = NBA_LINES_FILE,
                 memo_seconds: int = 600):
        self.league = league
        self.games_source = games_source or BalldontlieSource()
        self.injury_source = injury_source or EspnInjurySource()
        self.media_path = media_path
        self.lines_path = lines_path
        self.memo_seconds = memo_seconds
        self._data: tuple[float, NbaData] | None = None

    def data(self) -> NbaData:
        if self._data and time.time() - self._data[0] < self.memo_seconds:
            return self._data[1]
        d = collect(date.today(), self.games_source, self.injury_source)
        self._data = (time.time(), d)
        return d

    def team(self) -> list[Agent]:
        return [NbaDataAgent(self), NbaQualityAgent(), NbaTeamAgent(), NbaInjuryAgent(), NbaPredictorAgent(),
                NbaMediaAgent(self.media_path), NbaTotalsAgent(self.media_path, self.lines_path)]

    def lock_lines(self, today: date) -> dict:
        """경기 전날 밤(한국 시간) 실행: 미국 날짜가 today 인 경기 중 아직 기준점이 없는 것을 모델 총점으로 확정."""
        lines = load_lines(self.lines_path)
        new = 0
        for p in self.predict(today)["predictions"]:
            k = str(p["match_id"])
            if k in lines or p.get("actual_score"):
                continue
            lines[k] = {"line": ou_line(p["total"]), "model_total": p["total"], "home": p["home_team"],
                        "away": p["away_team"], "date": p["date"],
                        "set_at": datetime.now(timezone.utc).isoformat(timespec="minutes")}
            new += 1
        self.lines_path.parent.mkdir(parents=True, exist_ok=True)
        self.lines_path.write_text(json.dumps(lines, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"new": new, "total": len(lines)}

    def predict(self, target: date) -> dict:
        ctx = NbaContext(self.league, target)
        for agent in self.team():
            agent(ctx)
        return {
            "sport": "nba", "league": self.league.key, "league_name": self.league.name, "date": target.isoformat(),
            "matchdays": [], "data_source": " + ".join(s["name"] for s in ctx.data.status if s["ok"]),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "league_avg": {"home": round(ctx.mu + ctx.hca / 2, 1), "away": round(ctx.mu - ctx.hca / 2, 1)},
            "training_matches": len(ctx.history),
            "predictions": ctx.predictions, "teams": ctx.standings, "model": {
                "league_points": round(ctx.mu, 1), "home_advantage": round(ctx.hca, 1), "margin_sd": round(ctx.sigma, 1),
                **{k: v for k, v in asdict(NBA).items() if not k.startswith("request")}},
            "sources": ctx.data.status, "reconciliation": {}, "data_quality": ctx.data_quality,
            "media": ctx.media_report, "agent_log": ctx.log,
        }

    def calendar(self, today: date) -> list[dict]:
        season = season_of(today)
        days: dict[date, dict] = {}
        for g in self.data().games:
            if g.season != season:
                continue
            row = days.setdefault(g.date, {"date": g.date.isoformat(), "matchday": None, "matches": 0, "finished": 0})
            row["matches"] += 1
            row["finished"] += int(g.finished)
        return [days[d] for d in sorted(days)]

    def next_date(self, today: date) -> date | None:
        upcoming = [g.date for g in self.data().games if not g.finished and g.date >= today]
        return min(upcoming, default=None)

    def source_status(self) -> dict:
        return {"sources": self.data().status, "reconciliation": {}}

    def media_due(self, today: date, max_days: int = 2) -> list[NbaGame]:
        """매체 예측을 모을 경기: 오늘~max_days 일 안의 경기 중 아직 안 모은 것 + 최근 7일 끝난 경기 중 안 모은 것."""
        store = MediaStore(self.media_path)
        due = []
        for g in self.data().games:
            k = str(g.id)
            if k in store.collected:
                continue
            if not g.finished and 0 <= (g.date - today).days <= max_days:
                due.append(g)
            elif g.finished and 0 < (today - g.date).days <= 7:
                due.append(g)
        return sorted(due, key=lambda g: (g.finished, g.date))
