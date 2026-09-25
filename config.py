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

# THE POLLING LOOP (scanner/poll.py). Off until Phase C4 turns it on. While
# False, the scheduled job's `poll --ensure` does nothing and `poll --live`
# refuses. It is the only switch; flipping it is a commit.
POLLING_ENABLED = False

# What each metered call asks The Odds API for. A named list bills as one
# region per TEN books (v4 docs; measured), so ten books and three markets
# cost 3 credits a call. Pinnacle is the fair line; the other nine are NJ
# books (Bally Bet is the eleventh and would make it two regions).
ODDS_BOOKS = ["pinnacle", "draftkings", "fanduel", "betmgm", "williamhill_us",
              "betrivers", "espnbet", "fanatics", "hardrockbet", "betparx"]
ODDS_MARKETS = ["h2h", "spreads", "totals"]
# Price coverage only - none of these gets a forecasting model. MLB rejoins
# in March.
POLL_SPORTS = ["nfl", "nba", "nhl"]

# CREDITS, enforced in scanner/budget.py BEFORE every metered call.
#   The brief:   6,000 in total, spread over the days it expects to poll
#                (C4's three days, E, and F's four weeks).
#   Afterwards:  12,000 a month for the loop; with the props collector's
#                own 3,000 cap that is 15,000 of the 20,000 plan.
BRIEF_ACTIVE = True
CREDIT_CAP_BRIEF = 6000
BRIEF_POLL_DAYS = 42
POLL_MONTHLY_BUDGET = 12000

# Paper orders: the most any one strategy may put at risk in one ET day,
# in dollars (worst-case loss, fees included). Checked before every order.
STRATEGY_DAILY_EXPOSURE = {"default": 100.0}


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
