"""출처마다 다른 팀 이름 표기를 하나의 키로 맞춘다.

football-data.org: "Manchester City FC", "Brighton & Hove Albion FC"
API-Football    : "Manchester City", "Brighton"
openfootball    : "Manchester City FC", "Brighton & Hove Albion FC"
"""
from __future__ import annotations

import re
import unicodedata

# 정규화된 이름 → 공통 키
_ALIASES = {
    "brighton and hove albion": "brighton",
    "brighton hove albion": "brighton",
    "man city": "manchester city",
    "man utd": "manchester united",
    "man united": "manchester united",
    "manchester utd": "manchester united",
    "spurs": "tottenham",
    "tottenham hotspur": "tottenham",
    "wolverhampton wanderers": "wolves",
    "wolverhampton": "wolves",
    "nottm forest": "nottingham forest",
    "nott m forest": "nottingham forest",
    "leeds united": "leeds",
    "newcastle united": "newcastle",
    "west ham united": "west ham",
    "coventry city": "coventry",
    "ipswich town": "ipswich",
    "hull city": "hull",
    "leicester city": "leicester",
    "luton town": "luton",
    "sheffield utd": "sheffield united",
    "west bromwich albion": "west brom",
    "norwich city": "norwich",
    "stoke city": "stoke",
    "swansea city": "swansea",
    "cardiff city": "cardiff",
    "afc bournemouth": "bournemouth",
}

# 공통 키 → 화면 표시 이름, 약칭
DISPLAY = {
    "arsenal": ("Arsenal", "ARS"), "aston villa": ("Aston Villa", "AVL"), "bournemouth": ("Bournemouth", "BOU"),
    "brentford": ("Brentford", "BRE"), "brighton": ("Brighton", "BHA"), "burnley": ("Burnley", "BUR"),
    "chelsea": ("Chelsea", "CHE"), "coventry": ("Coventry City", "COV"), "crystal palace": ("Crystal Palace", "CRY"),
    "everton": ("Everton", "EVE"), "fulham": ("Fulham", "FUL"), "hull": ("Hull City", "HUL"),
    "ipswich": ("Ipswich Town", "IPS"), "leeds": ("Leeds United", "LEE"), "leicester": ("Leicester City", "LEI"),
    "liverpool": ("Liverpool", "LIV"), "manchester city": ("Manchester City", "MCI"),
    "manchester united": ("Manchester United", "MUN"), "newcastle": ("Newcastle United", "NEW"),
    "nottingham forest": ("Nottingham Forest", "NFO"), "southampton": ("Southampton", "SOU"),
    "sunderland": ("Sunderland", "SUN"), "tottenham": ("Tottenham Hotspur", "TOT"),
    "west ham": ("West Ham United", "WHU"), "wolves": ("Wolves", "WOL"),
    "luton": ("Luton Town", "LUT"), "sheffield united": ("Sheffield United", "SHU"),
}


def team_key(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ").replace("'", " ").replace(".", " ")
    s = re.sub(r"\b(a?fc)\b", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return _ALIASES.get(s, s)


def display(key: str, fallback: str = "", tla: str = "") -> tuple[str, str]:
    if key in DISPLAY:
        return DISPLAY[key]
    name = fallback or key.title()
    return name, (tla or re.sub(r"[^A-Z]", "", name.upper())[:3])
