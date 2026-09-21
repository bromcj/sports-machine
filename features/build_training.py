"""Assemble the training table from backfilled data and run walk-forward CV.

  python features/build_training.py

Joins, per historical final game:
  - bullpen quality/fatigue and offense via as-of merge (no future data)
  - the actual starter's shifted 5-start form via (game_pk, team)
  - rest days from the schedule, park factor from the home park

Baseline note (honesty): free odds tiers don't include historical closing
lines, so the market column here is a HOME-CONSTANT baseline (54%). Your
model must beat it decisively. True market comparison begins with the
closing lines your own daily runs are now archiving.
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
    starter_table, TEAM_ABBR, PARK_FACTORS)
from model.train import walk_forward

HOME_BASELINE = 0.54
OUT = ROOT / "data" / "training_mlb.parquet"


def load_games() -> pd.DataFrame:
    con = connect()
    g = pd.read_sql(
        "SELECT * FROM games WHERE sport='mlb' AND status='final'"
        " AND game_id LIKE 'mlb-%'", con)
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


def asof_join(games, table, team_col_games, value_cols, prefix):
    t = table.rename(columns={"team": "_team"}).sort_values("game_date")
    merged = pd.merge_asof(
        games.sort_values("game_date"), t,
        on="game_date", left_by=team_col_games, right_by="_team",
        direction="backward")
    return merged[value_cols].rename(
        columns={c: f"{prefix}_{c}" for c in value_cols})


def assemble() -> pd.DataFrame:
    games = load_games()
    print(f"{len(games)} final games loaded.")
    sc = load_statcast()
    print(f"{len(sc):,} Statcast pitches loaded.")
    lines = pitcher_game_lines(sc)
    pen, off, sp = bullpen_table(lines), offense_table(sc), starter_table(lines)

    df = games.copy().sort_values("game_date").reset_index(drop=True)
    for side in ("home", "away"):
        df = df.reset_index(drop=True)
        df[[f"{side}_pen_kbb_30d", f"{side}_pen_pitches_3d"]] = asof_join(
            df, pen, f"{side}_ab", ["pen_kbb_30d", "pen_pitches_3d"], side
        ).values
        df[[f"{side}_off_woba_30d"]] = asof_join(
            df, off, f"{side}_ab", ["off_woba_30d"], side).values
        s = sp.rename(columns={"pitch_team": f"{side}_ab"})
        df = df.merge(
            s.rename(columns={"sp_kbb_5s": f"{side}_sp_kbb_5s",
                              "sp_elite": f"{side}_sp_elite",
                              "sp_bottom": f"{side}_sp_bottom"}),
            on=["game_pk", f"{side}_ab"], how="left")
        # rest days
        prev = {}
        rest = []
        for _, r in df.iterrows():
            team, d = r[f"{side}_ab"], r["game_date"]
            rest.append((d - prev[team]).days if team in prev else np.nan)
            prev[team] = d
        df[f"{side}_rest_days"] = rest

    df["park_factor"] = df["home_ab"].map(PARK_FACTORS).fillna(1.0)
    df["novig_home_prob"] = HOME_BASELINE  # placeholder baseline; see docstring

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
        print("\nBar: logloss_model < logloss_market (home-constant baseline)."
              "\nBeat it here, then paper-trade vs REAL closing lines.")
    else:
        print("Need 3+ seasons backfilled for walk-forward CV. "
              "Run: python backfill.py")
