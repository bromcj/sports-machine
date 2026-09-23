"""Assemble the training table from backfilled data and run walk-forward CV.

  python features/build_training.py

Joins, per historical final game:
  - bullpen quality/fatigue and offense on (team, game_date); both tables are
    already shift(1)-ed at source, so a plain keyed merge is leakage-safe
  - the actual starter's shifted 5-start form via (game_pk, team)
  - rest days from the schedule
  - park factor for the home VENUE that season, computed from prior seasons

Every join is by explicit key. Never assign a merge result back positionally
(`.values`): pandas' default sort is not stable, so re-sorting a date-sorted
frame reshuffles same-day games and silently pairs each team with another
team's numbers.

Baseline note (honesty): free odds tiers don't include historical closing
lines, so the market column here is a HOME-CONSTANT baseline, not a market.
It is the home win rate of the seasons BEFORE the one being scored - the
same thing build_training_nfl.py uses, and leakage-free because a season
never contributes to its own baseline.

It used to be a flat 0.54, which was simply wrong: the actual rate over
this window is 52.3-52.9%. A miscalibrated constant is an easier target,
so part of any margin over it was the constant being off rather than the
model being good. True market comparison begins with the closing lines
your own daily runs are now archiving.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from db import connect
from features.sports.mlb_features import (
    load_statcast, pitcher_game_lines, bullpen_table, offense_table,
    starter_table, park_factor_table, venue_id, venue_series, TEAM_ABBR)
from model.train import walk_forward
from model.validation import record, explain, PLACEHOLDER
from model.persist import save as save_model, describe

# Fallback only, for the earliest season, which has no prior season to
# measure and is never scored anyway (walk_forward needs 2 training seasons).
HOME_BASELINE_PRIOR = 0.5
OUT = ROOT / "data" / "training_mlb.parquet"


def load_games() -> pd.DataFrame:
    con = connect()
    g = pd.read_sql(
        # 'mlb-espn-401817028' also matches 'mlb-%'. Those rows come from the
        # ESPN scoreboard fallback and carry no gamePk, so they cannot join to
        # Statcast; excluding them here keeps game_pk parseable as an int.
        "SELECT * FROM games WHERE sport='mlb' AND status='final'"
        " AND game_id LIKE 'mlb-%' AND game_id NOT LIKE 'mlb-espn-%'", con)
    con.close()
    g["game_pk"] = g["game_id"].str.replace("mlb-", "").astype(int)
    g["game_date"] = pd.to_datetime(g["game_date"])
    g["home_ab"] = g["home"].map(TEAM_ABBR)
    g["away_ab"] = g["away"].map(TEAM_ABBR)
    g = g.dropna(subset=["home_ab", "away_ab", "home_score", "away_score"])
    g["run_diff"] = g["home_score"] - g["away_score"]
    g["home_won"] = (g["run_diff"] > 0).astype(int)
    g["season"] = g["game_date"].dt.year
    return g.sort_values("game_date").reset_index(drop=True)


def rest_days(df: pd.DataFrame, team_col: str) -> list:
    """Days since that team last played ANY game. df must be sorted by date.

    It used to track only `df[team_col]`, so rest_days(df, "home_ab") measured
    days since the team's last HOME game - a homestand-length feature, not a
    rest feature. features/build.py:rest_days_asof has always checked both
    columns, so training and serving were computing DIFFERENT features under
    the same name.

    Measured over 12,052 games: 16% of rows disagreed, mean 4.04 days against
    live's 2.03, and the maximum ran to 197 days because a team's first away
    game of a season is months after its last away game of the previous one.

    Found while fixing the off-season window problem, not by looking for it -
    the impossible rest values that survived the window mask are what exposed
    it.
    """
    last, out = {}, []
    for away, home, d in zip(df["away_ab"], df["home_ab"], df["game_date"]):
        team = away if team_col == "away_ab" else home
        out.append((d - last[team]).days if team in last else np.nan)
        last[away] = d
        last[home] = d
    return out


def assemble() -> pd.DataFrame:
    games = load_games()
    print(f"{len(games)} final games loaded.")
    sc = load_statcast()
    print(f"{len(sc):,} Statcast pitches loaded.")
    lines = pitcher_game_lines(sc)
    pen, off, sp = bullpen_table(lines), offense_table(sc), starter_table(lines)

    df = games.copy()
    n = len(df)
    for side in ("home", "away"):
        ab = f"{side}_ab"
        df = df.merge(
            pen.rename(columns={"team": ab,
                                "pen_kbb_30d": f"{side}_pen_kbb_30d",
                                "pen_pitches_3d": f"{side}_pen_pitches_3d"}),
            on=[ab, "game_date"], how="left")
        df = df.merge(
            off.rename(columns={"team": ab,
                                "off_woba_30d": f"{side}_off_woba_30d"}),
            on=[ab, "game_date"], how="left")
        df = df.merge(
            sp.rename(columns={"pitch_team": ab,
                               "sp_kbb_5s": f"{side}_sp_kbb_5s"}),
            on=["game_pk", ab], how="left")
    if len(df) != n:
        raise AssertionError(
            f"joins changed the row count ({n} -> {len(df)}); a stat table has "
            f"duplicate keys and is fanning games out.")

    df = df.sort_values("game_date", kind="stable").reset_index(drop=True)
    for side in ("home", "away"):
        df[f"{side}_rest_days"] = rest_days(df, f"{side}_ab")

    # Park factor is keyed on VENUE and season, not team: the Athletics have
    # played in two parks inside this window and a static team->park map would
    # hand their Sacramento games the Oakland Coliseum's number.
    pf = park_factor_table(games)
    # Same rule as the table itself: the venue the game was actually played
    # at, from the Stats API, falling back to the map only where unknown.
    vmap = dict(zip(games["game_id"], venue_series(games)))
    df["venue"] = [vmap.get(gid) or v for gid, v in
                   zip(df["game_id"], (venue_id(t, s) for t, s
                                       in zip(df["home_ab"], df["season"])))]
    df["park_factor"] = [pf[(v, s)] for v, s in zip(df["venue"], df["season"])]
    # Placeholder baseline, expanding-window: each season is scored against the
    # home win rate of the seasons before it, never including itself. Matching
    # walk_forward's own train/test split keeps the comparison leakage-free.
    # Still a PLACEHOLDER - a well-calibrated constant is not a market, and
    # beating it clears nothing.
    seasons_sorted = sorted(df["season"].unique())
    prior_rate = {}
    for idx, yr in enumerate(seasons_sorted):
        earlier = df[df["season"].isin(seasons_sorted[:idx])]
        prior_rate[yr] = (float(earlier["home_won"].mean()) if len(earlier)
                          else HOME_BASELINE_PRIOR)
    df["novig_home_prob"] = df["season"].map(prior_rate)
    print("Placeholder baseline (home win rate of prior seasons): "
          + ", ".join(f"{y}:{prior_rate[y]:.3f}" for y in seasons_sorted))

    feats = [c for c in df.columns if any(
        c.startswith(p) for p in ("home_pen", "away_pen", "home_off", "away_off",
                                  "home_sp", "away_sp", "home_rest", "away_rest"))]
    feats.append("park_factor")
    before = len(df)
    df = df.dropna(subset=feats)
    print(f"{len(df)} games with complete features "
          f"({before - len(df)} early-window games dropped).")
    df.to_parquet(OUT)
    return df, feats


if __name__ == "__main__":
    df, feats = assemble()
    print(f"\nFeatures: {feats}\nTraining table saved -> {OUT.name}\n")
    if df["season"].nunique() >= 3:
        results = walk_forward(df, feats, target_col="run_diff",
                               season_col="season", sport="mlb")
        print(results.to_string(index=False))
        # Recorded as PLACEHOLDER: novig_home_prob is a 0.54 constant, not a
        # market. bets.engine keeps refusing MLB however well this scores,
        # which is correct - beating a constant is not evidence of edge.
        record("mlb", PLACEHOLDER,
               results.rename(columns={"test_season": "season"}).to_dict("records"))
        print()
        print(explain("mlb"))
        # Fit one final model on EVERY season and save it, so the daily run
        # can score games that have not been played. The alpha is the one the
        # most recent fold chose; k stays the config prior (k_fit says the
        # data has no better answer - see model/train.py).
        alpha = float(results.iloc[-1]["alpha"])
        path = save_model("mlb", df, feats, "run_diff", alpha)
        print(f"Saved model -> {path.relative_to(ROOT)}")
        print("  " + describe("mlb"))
        print()
        print("Bar: logloss_model < logloss_market on REAL de-vigged closing"
              " lines. The archive is collecting them now.")
    else:
        print("Need 3+ seasons backfilled for walk-forward CV. "
              "Run: python backfill.py")
