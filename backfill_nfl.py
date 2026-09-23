"""Backfill NFL data for model training.

  python backfill_nfl.py              -> seasons 2019-2026
  python backfill_nfl.py 2021 2026    -> custom range

Sources (all free, hosted on GitHub by nflverse):
  - schedules/results: nfldata games.csv (includes rest days, roof, div_game)
  - play-by-play EPA:  nfl_data_py import_pbp_data

Outputs:
  data/nfl/schedules.parquet        one row per game
  data/nfl/team_games.parquet       one row per team per game (EPA aggregates)
Resumable: skips pbp seasons already summarized.
"""
import sys
from pathlib import Path

import paths
import pandas as pd

PBP_URL = ("https://github.com/nflverse/nflverse-data/releases/download/"
           "pbp/play_by_play_{year}.parquet")

NFL_DIR = paths.NFL_DIR
SCHED_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
PBP_COLS = ["game_id", "posteam", "defteam", "epa", "pass", "rush", "success"]


def backfill(start: int = 2019, end: int = 2026):
    NFL_DIR.mkdir(parents=True, exist_ok=True)

    print("Downloading schedules 1999-present (small file)...")
    sched = pd.read_csv(SCHED_URL)
    sched = sched[(sched["season"] >= start) & (sched["season"] <= end)]
    sched.to_parquet(NFL_DIR / "schedules.parquet", index=False)
    print(f"  saved {len(sched)} games ({start}-{end}) -> schedules.parquet")

    tg_path = NFL_DIR / "team_games.parquet"
    done = set()
    if tg_path.exists():
        done = set(pd.read_parquet(tg_path)["season"].unique())
    frames = [pd.read_parquet(tg_path)] if tg_path.exists() else []

    for season in range(start, end + 1):
        if season in done:
            print(f"  [{season}] already summarized, skipping")
            continue
        print(f"  [{season}] downloading play-by-play (30-60s)...")
        pbp = pd.read_parquet(PBP_URL.format(year=season), columns=PBP_COLS)
        pbp = pbp[pbp["epa"].notna() & pbp["posteam"].notna()]

        off = (pbp.groupby(["game_id", "posteam"])
                  .agg(off_epa=("epa", "mean"),
                       off_pass_epa=("epa", lambda s: s[pbp.loc[s.index, "pass"] == 1].mean()),
                       off_sr=("success", "mean"),
                       plays=("epa", "size"))
                  .reset_index().rename(columns={"posteam": "team"}))
        dfn = (pbp.groupby(["game_id", "defteam"])
                  .agg(def_epa=("epa", "mean"))
                  .reset_index().rename(columns={"defteam": "team"}))
        tg = off.merge(dfn, on=["game_id", "team"], how="inner")
        tg["season"] = season
        frames.append(tg)
        print(f"  [{season}] {len(tg)} team-games summarized")

    all_tg = pd.concat(frames, ignore_index=True).drop_duplicates(["game_id", "team"])
    all_tg.to_parquet(tg_path, index=False)
    print(f"Backfill complete: {len(all_tg)} team-game rows -> team_games.parquet")
    print("Next: python features/build_training_nfl.py")


if __name__ == "__main__":
    a = [int(x) for x in sys.argv[1:3]]
    backfill(*(a or (2019, 2026)))
