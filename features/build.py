"""Feature engineering. Every rolling stat is computed AS-OF the game date
using only prior games (leakage guard). Data sources plug in here:

  - Statcast via pybaseball (works; enable cache)
  - FanGraphs advanced metrics: pybaseball's FG scraper is 403-broken (2026);
    use the FanGraphs JSON leaderboard API with an honest User-Agent,
    the `pybaseballstats` package, or a weekly manual CSV drop into data/fg/.

This module ships with the feature CONTRACT and leakage-safe rolling helpers.
Wire your source of choice into the loaders below.
"""
import json
import datetime as dt
import pandas as pd
import numpy as np
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
from db import connect

FEATURE_COLUMNS = [
    # Bullpen (hypothesized strongest class)
    "home_pen_quality", "away_pen_quality",        # gmLI-weighted xFIP/K-BB composite
    "home_pen_fatigue", "away_pen_fatigue",        # pitches last 3d, b2b arms
    # Offense — TEAM AGGREGATES, never lineup slots (kills slot-swap volatility)
    "home_wrc_plus_30d", "away_wrc_plus_30d",
    # Starting pitcher + tail treatment (hypothesis to TEST, not assume)
    "home_sp_xfip", "away_sp_xfip",
    "home_sp_elite_flag", "away_sp_elite_flag",    # top decile
    "home_sp_bottom_flag", "away_sp_bottom_flag",  # bottom decile
    # Context
    "park_factor", "home_rest_days", "away_rest_days", "away_travel_east",
    # September / edge flags
    "home_eliminated", "away_eliminated", "opener_flag", "pitch_limit_flag",
]


def rolling_asof(df: pd.DataFrame, group_col: str, value_col: str,
                 window: int, asof_col: str = "game_date") -> pd.Series:
    """Leakage-safe rolling mean: shift(1) so the current game never sees itself."""
    df = df.sort_values(asof_col)
    return (df.groupby(group_col)[value_col]
              .transform(lambda s: s.shift(1).rolling(window, min_periods=3).mean()))


def load_fangraphs_csv(name: str) -> pd.DataFrame:
    """Fallback loader for manual FanGraphs CSV exports dropped in data/fg/."""
    path = __import__("pathlib").Path(__file__).parent.parent / "data" / "fg" / name
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing. Export the FanGraphs leaderboard CSV weekly, "
            f"or wire the FG JSON API / pybaseballstats here.")
    return pd.read_csv(path)


def build_for_date(date: str | None = None):
    """Assemble the feature vector for each scheduled game and persist as JSON.

    NOTE: shipped as a wired skeleton — the contract, leakage guards, and
    persistence are done; connect real stat pulls where marked TODO.
    """
    date = date or dt.date.today().isoformat()
    con = connect()
    games = con.execute(
        "SELECT * FROM games WHERE game_date = ? AND status != 'final'", (date,)
    ).fetchall()
    ts = dt.datetime.utcnow().isoformat()
    for g in games:
        vec = {c: None for c in FEATURE_COLUMNS}
        # TODO: populate from Statcast/FG loaders using rolling_asof().
        con.execute(
            "INSERT OR REPLACE INTO features (game_id, asof_ts, payload) VALUES (?,?,?)",
            (g["game_id"], ts, json.dumps(vec)),
        )
    con.commit()
    con.close()
    print(f"Feature rows written for {len(games)} games on {date}.")


if __name__ == "__main__":
    build_for_date(sys.argv[1] if len(sys.argv) > 1 else None)
