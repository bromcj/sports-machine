"""nflverse player data for the receiving-props backtest. Free, cached.

    python props/nfl_data.py            # download + cache + summarise

nfl_data_py's own loader 404s against the current nflverse releases, so this
reads the release files directly and caches them under data/nfl/. Same data,
one less moving part, and the URL is visible rather than buried in a library
that has drifted from the upstream layout.

Nothing here costs credits. nflverse is free public data on GitHub.

WHAT IS USED, AND WHY EACH ONE.

  player_stats     targets, receptions, receiving yards, target share, by
                   player-week, keyed on gsis_id. This is both the OUTCOME
                   being predicted and the history the projection is built
                   from.
  snap_counts      offensive snap share. A receiver's target count is a
                   product of how often he is on the field and how often the
                   ball goes to him when he is; conflating them means a player
                   returning from injury looks like a player who lost his job.
  injuries         who was ruled out. B3's one genuinely plausible edge is
                   target redistribution when a team's WR1 is inactive, and
                   that requires knowing he was inactive BEFORE the game, not
                   inferring it afterwards from his zero snaps.

SEASON_TYPE. Regular season only, matching the props we bought. The postseason
is a different population - shorter benches, different game scripts - and
mixing it in would contaminate a 30-day form window at exactly the wrong time
of year.
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import paths

BASE = "https://github.com/nflverse/nflverse-data/releases/download"
SEASONS = (2023, 2024, 2025)
CACHE = paths.NFL_DIR

# nflverse renamed the weekly player file partway through this window: 2023-24
# are player_stats/player_stats_<season>, 2025 is stats_player/
# stats_player_week_<season>, with 150 columns instead of 53 and `team` where
# the old one said `recent_team`. Both spellings are tried, newest first, and
# the column is normalised below - a loader that silently returned two of three
# seasons is worse than one that fails, because the missing season looks like
# a modelling choice rather than a 404.
SOURCES = {
    "player_stats": [f"{BASE}/stats_player/stats_player_week_{{season}}.parquet",
                     f"{BASE}/player_stats/player_stats_{{season}}.parquet"],
    "snap_counts": [f"{BASE}/snap_counts/snap_counts_{{season}}.parquet"],
    "injuries": [f"{BASE}/injuries/injuries_{{season}}.parquet"],
}


def fetch(kind: str, seasons=SEASONS, refresh: bool = False) -> pd.DataFrame:
    out = CACHE / f"{kind}.parquet"
    if out.exists() and not refresh:
        return pd.read_parquet(out)
    frames, missing = [], []
    for s in seasons:
        got = None
        for tmpl in SOURCES[kind]:
            try:
                got = pd.read_parquet(tmpl.format(season=s))
                print(f"  {kind} {s} ... {len(got):,} rows")
                break
            except Exception:
                continue
        if got is None:
            missing.append(s)
            print(f"  {kind} {s} ... FAILED on every known URL")
            continue
        frames.append(got)
    if missing:
        raise SystemExit(
            f"{kind}: no file found for {missing}. Refusing to cache a "
            f"partial download - a silently missing season would look like a "
            f"modelling decision later.")
    # The two layouts disagree on the team column and on width; keep the
    # union and normalise the one name everything downstream depends on.
    df = pd.concat(frames, ignore_index=True)
    if "recent_team" in df.columns and "team" in df.columns:
        df["team"] = df["team"].fillna(df["recent_team"])
    elif "recent_team" in df.columns:
        df["team"] = df["recent_team"]
    CACHE.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    return df


def receiving_weeks(refresh: bool = False) -> pd.DataFrame:
    """One row per (player, season, week) for anyone who could catch a pass."""
    d = fetch("player_stats", refresh=refresh)
    d = d[(d["season_type"] == "REG") & d["season"].isin(SEASONS)].copy()
    # Positions that receive. A quarterback with one reception is noise and a
    # book does not post a receptions line on him.
    d = d[d["position"].isin(["WR", "TE", "RB", "FB"])]
    keep = ["player_id", "player_display_name", "position", "team",
            "opponent_team", "season", "week", "targets", "receptions",
            "receiving_yards", "target_share", "air_yards_share",
            "receiving_air_yards"]
    d = d[[c for c in keep if c in d.columns]].copy()
    for c in ("targets", "receptions", "receiving_yards"):
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0.0)
    return d


def snaps(refresh: bool = False) -> pd.DataFrame:
    d = fetch("snap_counts", refresh=refresh)
    d = d[(d["game_type"] == "REG") & d["season"].isin(SEASONS)].copy()
    keep = ["pfr_player_id", "player", "position", "team", "season", "week",
            "offense_snaps", "offense_pct"]
    return d[[c for c in keep if c in d.columns]]


def inactives(refresh: bool = False) -> pd.DataFrame:
    """Players ruled OUT before kickoff. Known in advance, not inferred after.

    `report_status` is the pre-game designation. Using post-game snap counts to
    decide who was out would leak: a player can be active and simply not play,
    and more importantly the market knew the designation and we would be
    pretending to know more than it did.
    """
    d = fetch("injuries", refresh=refresh)
    d = d[d["season"].isin(SEASONS)].copy()
    if "report_status" not in d.columns:
        return pd.DataFrame(columns=["gsis_id", "season", "week", "team"])
    out = d[d["report_status"].astype(str).str.lower().eq("out")]
    keep = [c for c in ("gsis_id", "season", "week", "team", "position",
                        "full_name") if c in out.columns]
    return out[keep].drop_duplicates()


if __name__ == "__main__":
    print("fetching nflverse data (free, cached under data/nfl/)")
    r = receiving_weeks()
    print(f"\nreceiving weeks: {len(r):,} rows, "
          f"{r['player_id'].nunique():,} players")
    print(r.groupby("season").agg(rows=("targets", "size"),
                                  targets=("targets", "sum"),
                                  rec=("receptions", "sum"),
                                  yds=("receiving_yards", "sum")).to_string())
    s = snaps()
    print(f"\nsnap counts: {len(s):,} rows")
    i = inactives()
    print(f"ruled OUT before kickoff: {len(i):,} player-weeks")
    print("\nper-position share of targets")
    print((r.groupby("position")["targets"].sum()
           / r["targets"].sum()).round(3).to_string())
