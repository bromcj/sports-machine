"""nflverse abbreviation -> The Odds API team name. Validated, not assumed.

The three feeds mint incompatible game ids and CLAUDE.md says so; they also
spell teams differently, and that is the quieter problem. nflverse says "KC",
the Odds API says "Kansas City Chiefs", and nothing errors when they fail to
match - the game simply is not there.

This project has already paid for that once. The Athletics dropped "Oakland"
from their name in 2025, the Stats API changed and the odds feed did not until
2026, and matching on the raw string silently lost 169 games - 7% of the
season, and not a random 7%: one distinctive team, entirely absent.

So this table exists, and props/b3_dryrun.py checks it against a live events
list before any credits are spent, rather than after a backfill comes back
thin.
"""

# Current teams. 2023-2025 uses only these; the relocations below are for
# older seasons and for feeds that lag a rename.
ABBR_TO_NAME = {
    "ARI": "Arizona Cardinals",       "ATL": "Atlanta Falcons",
    "BAL": "Baltimore Ravens",        "BUF": "Buffalo Bills",
    "CAR": "Carolina Panthers",       "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals",      "CLE": "Cleveland Browns",
    "DAL": "Dallas Cowboys",          "DEN": "Denver Broncos",
    "DET": "Detroit Lions",           "GB": "Green Bay Packers",
    "HOU": "Houston Texans",          "IND": "Indianapolis Colts",
    "JAX": "Jacksonville Jaguars",    "KC": "Kansas City Chiefs",
    "LA": "Los Angeles Rams",         "LAC": "Los Angeles Chargers",
    "LV": "Las Vegas Raiders",        "MIA": "Miami Dolphins",
    "MIN": "Minnesota Vikings",       "NE": "New England Patriots",
    "NO": "New Orleans Saints",       "NYG": "New York Giants",
    "NYJ": "New York Jets",           "PHI": "Philadelphia Eagles",
    "PIT": "Pittsburgh Steelers",     "SEA": "Seattle Seahawks",
    "SF": "San Francisco 49ers",      "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans",        "WAS": "Washington Commanders",
}

# Spellings the same club has had, or that a feed still uses. Kept separate so
# the current table stays readable and so a genuinely NEW abbreviation shows up
# as unmapped rather than quietly resolving to something plausible.
ALIASES = {
    "LAR": "LA",     # nflverse uses LA; some sources use LAR
    "OAK": "LV",     # Raiders, pre-2020
    "SD": "LAC",     # Chargers, pre-2017
    "STL": "LA",     # Rams, pre-2016
    "WSH": "WAS",
    "JAC": "JAX",
    "ARZ": "ARI",
    "BLT": "BAL",
    "CLV": "CLE",
    "HST": "HOU",
}

NAME_TO_ABBR = {v: k for k, v in ABBR_TO_NAME.items()}


def to_name(abbr: str) -> str | None:
    """nflverse abbreviation -> Odds API team name, or None if unknown."""
    if not abbr:
        return None
    a = str(abbr).strip().upper()
    a = ALIASES.get(a, a)
    return ABBR_TO_NAME.get(a)


def to_abbr(name: str) -> str | None:
    """Odds API team name -> nflverse abbreviation, or None if unknown."""
    return NAME_TO_ABBR.get(str(name).strip()) if name else None
