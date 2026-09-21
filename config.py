"""Sport registry. Add or disable leagues here — everything else reads this.

margin      : what the model predicts (home minus away)
k_default   : starting logistic steepness for margin -> win prob
              (refit per sport in model/calibrate.py; these are sane priors)
min_edge    : per-sport bet threshold vs no-vig market prob
months      : in-season months (used by run_daily to skip dead leagues)
espn        : ESPN scoreboard path for schedule/finals (keyless)
odds_key    : The Odds API sport key
"""

SPORTS = {
    "mlb": {
        "active": True,
        "odds_key": "baseball_mlb",
        "espn": "baseball/mlb",
        "margin": "run_diff",
        "k_default": 0.30,
        "min_edge": 0.035,
        "months": [3, 4, 5, 6, 7, 8, 9, 10],
    },
    "nfl": {
        "active": True,
        "odds_key": "americanfootball_nfl",
        "espn": "football/nfl",
        "margin": "point_diff",
        "k_default": 0.16,
        "min_edge": 0.040,   # sharpest US market; demand more edge
        "months": [9, 10, 11, 12, 1, 2],
    },
    "nba": {
        "active": True,
        "odds_key": "basketball_nba",
        "espn": "basketball/nba",
        "margin": "point_diff",
        "k_default": 0.13,
        "min_edge": 0.040,
        "months": [10, 11, 12, 1, 2, 3, 4, 5, 6],
    },
    "nhl": {
        "active": True,
        "odds_key": "icehockey_nhl",
        "espn": "hockey/nhl",
        "margin": "goal_diff",
        "k_default": 0.85,
        "min_edge": 0.035,
        "months": [10, 11, 12, 1, 2, 3, 4, 5, 6],
    },
    # Flip active=True to expand. Feature modules required first.
    "ncaaf": {
        "active": False,
        "odds_key": "americanfootball_ncaaf",
        "espn": "football/college-football",
        "margin": "point_diff",
        "k_default": 0.14,
        "min_edge": 0.045,
        "months": [8, 9, 10, 11, 12, 1],
    },
    "ncaab": {
        "active": False,
        "odds_key": "basketball_ncaab",
        "espn": "basketball/mens-college-basketball",
        "margin": "point_diff",
        "k_default": 0.13,
        "min_edge": 0.045,
        "months": [11, 12, 1, 2, 3, 4],
    },
}


def active_sports(month: int | None = None) -> list[str]:
    """Sports switched on, optionally filtered to those in season."""
    out = []
    for name, cfg in SPORTS.items():
        if not cfg["active"]:
            continue
        if month is not None and month not in cfg["months"]:
            continue
        out.append(name)
    return out
