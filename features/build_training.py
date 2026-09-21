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
    starter_table, park_factor_table, venue_id, TEAM_ABBR)
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


def rest_days(df: pd.DataFrame, team_col: str) -> list:
    """Days since that team last played. df must be sorted by game_date."""
    prev, out = {}, []
    for team, d in zip(df[team_col], df["game_date"]):
        out.append((d - prev[team]).days if team in prev else np.nan)
        prev[team] = d
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
                               "sp_kbb_5s": f"{side}_sp_kbb_5s",
                               "sp_elite": f"{side}_sp_elite",
                               "sp_bottom": f"{side}_sp_bottom"}),
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
    df["venue"] = [venue_id(t, s) for t, s in zip(df["home_ab"], df["season"])]
    df["park_factor"] = [pf[(v, s)] for v, s in zip(df["venue"], df["season"])]
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
