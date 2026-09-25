"""Sport registry. Add or disable leagues here — everything else reads this.

k_default   : logistic steepness for margin -> win prob. MLB trains, grades
              and serves with this value as is (model/train.py reports a
              fitted k_fit only as a diagnostic); only the NFL walk-forward
              refits k per fold, via model/calibrate.py
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
        "k_default": 0.30,
        "min_edge": 0.035,
        "months": [3, 4, 5, 6, 7, 8, 9, 10],
    },
    "nfl": {
        "active": True,
        "odds_key": "americanfootball_nfl",
        "espn": "football/nfl",
        "k_default": 0.16,
        "min_edge": 0.040,   # sharpest US market; demand more edge
        "months": [9, 10, 11, 12, 1, 2],
    },
    "nba": {
        "active": True,
        "odds_key": "basketball_nba",
        "espn": "basketball/nba",
        "k_default": 0.13,
        "min_edge": 0.040,
        "months": [10, 11, 12, 1, 2, 3, 4, 5, 6],
    },
    "nhl": {
        "active": True,
        "odds_key": "icehockey_nhl",
        "espn": "hockey/nhl",
        "k_default": 0.85,
        "min_edge": 0.035,
        "months": [10, 11, 12, 1, 2, 3, 4, 5, 6],
    },
}


# ---------------------------------------------------------------- scanner ---
# docs/briefs/2026-09-25-next-task.md. Everything the scanner is allowed to do
# is switched here, in one place.

# LEGAL KILL SWITCH. Kalshi's sports contracts are open to a NJ resident under
# a court ruling that could change. False disables every Kalshi sports path at
# once - adapters, polling, fair value, paper orders - through the single check
# in scanner.venues.allowed(). Non-sports Kalshi markets are unaffected.
KALSHI_SPORTS_ENABLED = True


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
