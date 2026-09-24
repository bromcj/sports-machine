"""NFL features — real implementation from nflverse data (v1).

Inputs (created by backfill_nfl.py):
  data/nfl/schedules.parquet    rest days, roof, div_game, scores, market lines
  data/nfl/team_games.parquet   per-team-game EPA aggregates

Leakage guard: all rolling stats are shift(1) — a game never sees itself.
Windows roll across season boundaries (early-season games use last year's
form), with a minimum of 4 prior games before a value is emitted.
"""
import pandas as pd
import numpy as np

import paths

NFL_DIR = paths.NFL_DIR

ROLL_N = 8          # games of form
MIN_PRIOR = 4       # required history before features emit

FEATURE_COLUMNS = [
    "home_off_epa", "away_off_epa",
    "home_def_epa", "away_def_epa",        # lower (more negative) = better D
    "home_pass_epa", "away_pass_epa",
    "home_sr", "away_sr",                  # success rate
    "home_rest", "away_rest",
    "dome", "div_game",
]


def team_form() -> pd.DataFrame:
    """Rolling, shifted form table: one row per (team, game_id)."""
    sched = pd.read_parquet(NFL_DIR / "schedules.parquet")
    tg = pd.read_parquet(NFL_DIR / "team_games.parquet")
    order = sched[["game_id", "gameday"]]
    tg = tg.merge(order, on="game_id", how="left").sort_values(["team", "gameday"])

    for col, out in [("off_epa", "f_off_epa"), ("def_epa", "f_def_epa"),
                     ("off_pass_epa", "f_pass_epa"), ("off_sr", "f_sr")]:
        tg[out] = (tg.groupby("team")[col]
                     .transform(lambda s: s.shift(1)
                                .rolling(ROLL_N, min_periods=MIN_PRIOR).mean()))
    return tg[["game_id", "team", "f_off_epa", "f_def_epa", "f_pass_epa", "f_sr"]]


def build_table() -> pd.DataFrame:
    """One row per game with features, target, and market baselines."""
    sched = pd.read_parquet(NFL_DIR / "schedules.parquet")
    sched = sched[sched["game_type"] == "REG"].copy()
    form = team_form()

    df = (sched
          .merge(form.add_prefix("home_"), left_on=["game_id", "home_team"],
                 right_on=["home_game_id", "home_team"], how="left")
          .merge(form.add_prefix("away_"), left_on=["game_id", "away_team"],
                 right_on=["away_game_id", "away_team"], how="left"))

    out = pd.DataFrame({
        "game_id": df["game_id"], "season": df["season"],
        "gameday": df["gameday"],
        "home_off_epa": df["home_f_off_epa"], "away_off_epa": df["away_f_off_epa"],
        "home_def_epa": df["home_f_def_epa"], "away_def_epa": df["away_f_def_epa"],
        "home_pass_epa": df["home_f_pass_epa"], "away_pass_epa": df["away_f_pass_epa"],
        "home_sr": df["home_f_sr"], "away_sr": df["away_f_sr"],
        "home_rest": df["home_rest"], "away_rest": df["away_rest"],
        "dome": df["roof"].isin(["dome", "closed"]).astype(int),
        "div_game": df["div_game"].fillna(0).astype(int),
        "point_diff": df["home_score"] - df["away_score"],
        "home_won": (df["home_score"] > df["away_score"]).astype(float),
    })

    # Real market baseline where the schedule file carries moneylines
    if {"home_moneyline", "away_moneyline"}.issubset(df.columns):
        hp = np.where(df["home_moneyline"] > 0,
                      100 / (df["home_moneyline"] + 100),
                      -df["home_moneyline"] / (-df["home_moneyline"] + 100))
        ap = np.where(df["away_moneyline"] > 0,
                      100 / (df["away_moneyline"] + 100),
                      -df["away_moneyline"] / (-df["away_moneyline"] + 100))
        out["novig_home_prob"] = hp / (hp + ap)
    if "spread_line" in df.columns:
        out["market_spread"] = df["spread_line"]

    return out


# Ideas carried over from the earlier nfl_features.py stub, which this module
# replaced. None are implemented here, and each would need a new data source:
#   QB1 confirmed vs backup     (moves an NFL line more than anything else)
#   travel / time-zone crossing
#   dome and wind over 15mph    (dome is in, wind is not)
#   late-season elimination     (a team with nothing to play for)
