"""Rebuild a richer Statcast extract from the local pybaseball cache. Free, offline.

    python research/enrich_statcast.py

WHY THIS EXISTS.

`backfill.py:backfill_statcast` keeps 13 columns. That was the right call for
the moneyline model - it only ever needed strikeouts, walks and wOBA - but it
throws away everything needed to measure a pitcher by his *process* rather than
his results:

    release_speed        is he throwing slower than last year?
    pitch_type           has the mix changed?
    p_throws / stand     platoon, which is most of a reliever's value
    estimated_woba...    contact quality, which stabilises far faster than wOBA
    delta_home_win_exp   leverage, i.e. which reliever the manager trusts
    pitcher_days_..._prev_game   fatigue, without reconstructing it from dates

docs/experiments.md E1 and E6 both need these. The obvious way to get them is
to re-download five seasons from Baseball Savant, which takes hours and hammers
a free public service.

It turns out we already have them. pybaseball caches every HTTP response it
makes, and `cache.enable()` was on for the original backfill, so
~/.pybaseball/cache holds the FULL 119-column response for all 903 days it ever
fetched - 3.55 M pitches across 2022-2026. The trimming happened after the
cache write, on the way to disk. So this is a local re-extraction with a wider
column list, not a download: no network, no rate limit, nothing to spend.

WHAT IT DOES NOT CHANGE.

It writes to data/statcast_rich/ and leaves data/statcast/ exactly as it is.
The trained model reads the latter and must keep seeing byte-identical inputs;
a research script is not a reason to perturb the thing the gates are judged on.
The two are cross-checked at the end of this run - same pitch counts per
season, or it says so loudly.
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import paths

CACHE = Path.home() / ".pybaseball" / "cache"
OUT_DIR = paths.DATA_DIR / "statcast_rich"

# Everything the 13-column extract has, plus what E1/E6 need. Kept explicit
# rather than "all 119": the wide frame is ~10x the size and most of it is
# ball-tracking telemetry no hypothesis in experiments.md mentions.
KEEP = [
    # --- the original 13, so this file is a superset and can be diffed ---
    "game_pk", "game_date", "home_team", "away_team", "inning",
    "inning_topbot", "at_bat_number", "pitch_number", "pitcher", "batter",
    "events", "woba_value", "woba_denom",
    # --- process: how the pitch was thrown, not how it turned out ---
    "release_speed", "pitch_type", "release_spin_rate", "release_extension",
    # --- matchup ---
    "p_throws", "stand",
    # --- contact quality: stabilises in ~50 BBE where wOBA needs ~300 PA ---
    "estimated_woba_using_speedangle",
    # --- leverage and fatigue, for the bullpen measures in E1 ---
    "delta_home_win_exp", "home_win_exp", "pitcher_days_since_prev_game",
    "n_thruorder_pitcher", "outs_when_up", "bat_score", "fld_score",
    # --- housekeeping ---
    "game_type", "player_name", "description",
]

# Regular season only. Spring training pitchers are not the same population and
# the postseason bullpen is not the regular-season bullpen; both would bias a
# 30-day window that happens to straddle them.
REGULAR = "R"


def _cache_files() -> list[Path]:
    return sorted(CACHE.glob("get_statcast_data_from_csv_url*.parquet"))


def build() -> dict:
    files = _cache_files()
    if not files:
        raise FileNotFoundError(
            f"No pybaseball cache in {CACHE}. This script cannot download; it "
            f"only re-extracts what was already fetched.")
    print(f"reading {len(files)} cached responses from {CACHE}")

    frames, missing = [], set()
    for i, f in enumerate(files, 1):
        if i % 200 == 0:
            print(f"  {i}/{len(files)}")
        d = pd.read_parquet(f)
        if len(d) == 0:
            continue
        missing |= {c for c in KEEP if c not in d.columns}
        frames.append(d[[c for c in KEEP if c in d.columns]])
    if missing:
        print(f"  note: columns absent from at least one response: "
              f"{sorted(missing)}")

    sc = pd.concat(frames, ignore_index=True)
    print(f"concatenated {len(sc):,} rows")

    # pybaseball splits a date range into overlapping chunks, so the same pitch
    # can appear in more than one cached response. A pitch is identified by
    # (game_pk, at_bat_number, pitch_number) - not by game_date, which is the
    # same for every pitch in a game.
    before = len(sc)
    sc = sc.drop_duplicates(subset=["game_pk", "at_bat_number", "pitch_number"])
    print(f"deduped {before - len(sc):,} repeated pitches -> {len(sc):,}")

    sc["game_date"] = pd.to_datetime(sc["game_date"])
    sc = sc[sc["game_type"] == REGULAR]
    sc["season"] = sc["game_date"].dt.year
    print(f"regular season only -> {len(sc):,}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tally = {}
    for year, part in sc.groupby("season"):
        out = OUT_DIR / f"{year}.parquet"
        # Written as an ISO string, matching how backfill_statcast() writes it.
        # The two extracts get compared and mixed; a dtype that differs between
        # them is exactly the drift that bit the Statcast top-up before.
        part = part.copy()
        part["game_date"] = part["game_date"].dt.strftime("%Y-%m-%d")
        part.sort_values(["game_pk", "at_bat_number", "pitch_number"]) \
            .reset_index(drop=True).to_parquet(out)
        tally[int(year)] = len(part)
        print(f"  {year}: {len(part):>9,} pitches -> {out.name} "
              f"({out.stat().st_size / 1e6:.0f} MB)")
    return tally


def crosscheck(tally: dict) -> bool:
    """The rich extract must agree with the one the model actually trains on.

    Not a formality. If these disagree, every E1/E6 number is computed on a
    different population than the model and the market were, and the whole of
    Part E would be comparing two things that are not the same thing.
    """
    print("\ncross-check against data/statcast/ (the extract the model uses)")
    ok = True
    for year in sorted(tally):
        old = paths.STATCAST_DIR / f"{year}.parquet"
        if not old.exists():
            print(f"  {year}: no existing extract to compare")
            continue
        o = pd.read_parquet(old, columns=["game_pk", "at_bat_number",
                                          "pitch_number"])
        o = o.drop_duplicates()
        n_old, n_new = len(o), tally[year]
        delta = n_new - n_old
        pct = 100 * delta / n_old if n_old else 0
        flag = "" if abs(pct) < 0.5 else "   <-- LOOK"
        if abs(pct) >= 0.5:
            ok = False
        print(f"  {year}: existing {n_old:>9,}   rich {n_new:>9,}   "
              f"{delta:>+8,} ({pct:+.2f}%){flag}")
    return ok


if __name__ == "__main__":
    t = build()
    ok = crosscheck(t)
    print("\ntotal", f"{sum(t.values()):,}", "pitches")
    if not ok:
        print("\nA season differs by more than 0.5%. Find out why before using "
              "this for anything - the likely causes are a postseason/spring "
              "filter difference or a cache that stops short of the last "
              "top-up.")
