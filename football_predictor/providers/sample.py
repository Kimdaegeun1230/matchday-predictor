"""오프라인 샘플 공급자.

API 키 없이 파이프라인을 시험하기 위한 가상 시즌 데이터.
구단 이름만 실제이고, 경기 결과·선수·부상은 모두 시드 고정 시뮬레이션이다.
기준일(as_of)이 항상 경기일이 되도록 일정을 맞춘다.
"""
from __future__ import annotations

import math
import random
from datetime import date, datetime, time, timedelta, timezone

from ..config import League
from ..models import Match, Player, Team
from .base import DataProvider

# (이름, 약칭, 숨은 전력 -1.0 ~ +1.0)
_CLUBS: dict[str, list[tuple[str, str, float]]] = {
    "EPL": [
        ("Arsenal FC", "ARS", 0.85), ("Manchester City FC", "MCI", 0.85), ("Liverpool FC", "LIV", 0.9),
        ("Chelsea FC", "CHE", 0.6), ("Newcastle United FC", "NEW", 0.5), ("Aston Villa FC", "AVL", 0.45),
        ("Tottenham Hotspur FC", "TOT", 0.4), ("Manchester United FC", "MUN", 0.35),
        ("Brighton & Hove Albion FC", "BHA", 0.3), ("Nottingham Forest FC", "NOT", 0.25),
        ("AFC Bournemouth", "BOU", 0.2), ("Crystal Palace FC", "CRY", 0.15), ("Fulham FC", "FUL", 0.1),
        ("Brentford FC", "BRE", 0.1), ("Everton FC", "EVE", 0.0), ("West Ham United FC", "WHU", -0.05),
        ("Wolverhampton Wanderers FC", "WOL", -0.2), ("Leeds United FC", "LEE", -0.3),
        ("Sunderland AFC", "SUN", -0.35), ("Burnley FC", "BUR", -0.45),
    ],
    "LALIGA": [
        ("FC Barcelona", "FCB", 0.9), ("Real Madrid CF", "RMA", 0.9), ("Club Atlético de Madrid", "ATM", 0.65),
        ("Athletic Club", "ATH", 0.45), ("Villarreal CF", "VIL", 0.45), ("Real Betis Balompié", "BET", 0.35),
        ("Real Sociedad de Fútbol", "RSO", 0.25), ("RC Celta de Vigo", "CEL", 0.2), ("CA Osasuna", "OSA", 0.1),
        ("Rayo Vallecano de Madrid", "RAY", 0.1), ("Sevilla FC", "SEV", 0.05), ("Getafe CF", "GET", 0.0),
        ("Valencia CF", "VAL", 0.0), ("RCD Espanyol de Barcelona", "ESP", -0.05), ("Girona FC", "GIR", -0.1),
        ("RCD Mallorca", "MLL", -0.1), ("Deportivo Alavés", "ALA", -0.15), ("Elche CF", "ELC", -0.3),
        ("Levante UD", "LEV", -0.35), ("Real Oviedo", "OVI", -0.4),
    ],
    "BUNDESLIGA": [
        ("FC Bayern München", "FCB", 0.95), ("Bayer 04 Leverkusen", "B04", 0.55),
        ("Borussia Dortmund", "BVB", 0.6), ("RB Leipzig", "RBL", 0.45), ("Eintracht Frankfurt", "SGE", 0.4),
        ("VfB Stuttgart", "VFB", 0.4), ("SC Freiburg", "SCF", 0.2), ("1. FSV Mainz 05", "M05", 0.15),
        ("VfL Wolfsburg", "WOB", 0.05), ("Borussia Mönchengladbach", "BMG", 0.05),
        ("TSG 1899 Hoffenheim", "TSG", 0.05), ("SV Werder Bremen", "SVW", 0.0),
        ("FC Augsburg", "FCA", -0.1), ("1. FC Union Berlin", "FCU", -0.1), ("1. FC Köln", "KOE", -0.2),
        ("FC St. Pauli", "STP", -0.25), ("Hamburger SV", "HSV", -0.25), ("1. FC Heidenheim 1846", "HDH", -0.35),
    ],
}

_FIRST = ["Alex", "Ben", "Carlos", "David", "Erik", "Felix", "Gabriel", "Hugo", "Ivan", "Jonas", "Kai",
          "Luca", "Mateo", "Nico", "Oscar", "Pablo", "Rafael", "Sam", "Tom", "Victor", "Yusuf", "Min-jae"]
_LAST = ["Silva", "Müller", "García", "Smith", "Kim", "Rossi", "Johnson", "Fernández", "Schmidt", "Martin",
         "López", "Becker", "Walker", "Moreno", "Wagner", "Diallo", "Costa", "Hoffmann", "Ruiz", "Park"]
_SQUAD_SHAPE = [("Goalkeeper", 2), ("Defence", 7), ("Midfield", 7), ("Offence", 4)]
_POS_GOAL_WEIGHT = {"Goalkeeper": 0.0, "Defence": 0.4, "Midfield": 1.0, "Offence": 2.6}


def _poisson(rng: random.Random, lam: float) -> int:
    l, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= l:
            return k
        k += 1


def _round_robin(n: int) -> list[list[tuple[int, int]]]:
    """원형 방식 더블 라운드로빈 (홈/원정 교대)."""
    idx = list(range(n))
    rounds = []
    for r in range(n - 1):
        pairs = []
        for i in range(n // 2):
            a, b = idx[i], idx[n - 1 - i]
            pairs.append((a, b) if (r + i) % 2 == 0 else (b, a))
        rounds.append(pairs)
        idx = [idx[0]] + [idx[-1]] + idx[1:-1]
    return rounds + [[(b, a) for a, b in rnd] for rnd in rounds]


class SampleProvider(DataProvider):
    name = "sample (simulated)"

    def __init__(self, seed: int = 2026):
        self.seed = seed
        self._as_of = date.today()
        self._cache: dict[tuple[str, date], tuple] = {}

    def _season(self, league: League, as_of: date):
        key = (league.key, as_of)
        if key in self._cache:
            return self._cache[key]
        rng = random.Random(f"{self.seed}-{league.key}-{as_of.year if as_of.month >= 7 else as_of.year - 1}")
        clubs = _CLUBS[league.key]
        base_id = {"EPL": 1000, "LALIGA": 2000, "BUNDESLIGA": 3000}[league.key]
        teams = {base_id + i: Team(base_id + i, n, n.replace(" FC", "").replace("FC ", ""), t)
                 for i, (n, t, _) in enumerate(clubs)}
        power = {base_id + i: p for i, (_, _, p) in enumerate(clubs)}
        ids = list(teams)

        # 기준일이 경기일(matchday m)이 되도록 주간 일정을 배치
        season_start = date(as_of.year if as_of.month >= 7 else as_of.year - 1, 8, 15)
        m = max(0, (as_of - season_start).days // 7)
        rounds = _round_robin(len(ids))
        m = min(m, len(rounds) - 1)

        matches: list[Match] = []
        goals_by_team: dict[int, int] = {tid: 0 for tid in ids}
        for r, pairs in enumerate(rounds):
            day = as_of + timedelta(days=7 * (r - m))
            for j, (hi, ai) in enumerate(pairs):
                h, a = ids[hi], ids[ai]
                kickoff = datetime.combine(day, time(12 + 2 * (j % 4), 30), tzinfo=timezone.utc)
                mid = base_id * 100 + r * 20 + j
                if r < m:
                    lam_h = 1.45 * math.exp(0.55 * (power[h] - power[a]) + 0.1)
                    lam_a = 1.15 * math.exp(0.55 * (power[a] - power[h]) - 0.1)
                    hg, ag = _poisson(rng, lam_h), _poisson(rng, lam_a)
                    goals_by_team[h] += hg
                    goals_by_team[a] += ag
                    matches.append(Match(mid, kickoff, teams[h], teams[a], "FINISHED", r + 1, hg, ag))
                else:
                    matches.append(Match(mid, kickoff, teams[h], teams[a], "SCHEDULED", r + 1))

        players = self._squads(rng, teams, goals_by_team, m)
        self._cache[key] = (teams, matches, players)
        return self._cache[key]

    def _squads(self, rng, teams, goals_by_team, games_played) -> dict[int, list[Player]]:
        out: dict[int, list[Player]] = {}
        pid = 0
        for tid, team in teams.items():
            squad: list[Player] = []
            for pos, count in _SQUAD_SHAPE:
                for _ in range(count):
                    pid += 1
                    name = f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"
                    squad.append(Player(tid * 100 + pid % 100, name, tid, pos, played=games_played))
            # 팀 득점을 포지션 가중치 + 개인 편차로 분배
            weights = [_POS_GOAL_WEIGHT[p.position] * rng.uniform(0.3, 1.7) for p in squad]
            for _ in range(goals_by_team[tid]):
                scorer = rng.choices(squad, weights=weights)[0]
                scorer.goals += 1
                if rng.random() < 0.7:
                    rng.choice([p for p in squad if p is not scorer and p.position != "Goalkeeper"]).assists += 1
            # 결장자 0~2명
            for p in rng.sample(squad, rng.choice([0, 1, 1, 2])):
                p.play_prob = 0.0
                p.absence_reason = rng.choice(["injury", "injury", "suspension"])
            out[tid] = squad
        return out

    def get_teams(self, league: League) -> dict[int, Team]:
        return self._season(league, self._as_of)[0]

    def get_matches(self, league: League, as_of: date) -> list[Match]:
        self._as_of = as_of
        return self._season(league, as_of)[1]

    def get_players(self, league: League, teams: dict[int, Team]) -> dict[int, list[Player]]:
        return self._season(league, self._as_of)[2]
