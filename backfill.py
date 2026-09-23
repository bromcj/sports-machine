"""Backfill historical MLB data for model training.

  python backfill.py            -> seasons 2022-2026
  python backfill.py 2023 2026  -> custom range

Two things happen per season:
  1. Full schedule + final scores from the MLB Stats API -> games table
  2. Full pitch-level Statcast download via pybaseball -> data/statcast/{year}.parquet

The Statcast downloads are LARGE (several hundred MB per season) and slow
(30-60+ min per season on home internet). Start it and walk away —
it is resumable: seasons already on disk are skipped.
"""
import sys
import datetime as dt
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).parent))
from db import connect

SCHED = "https://statsapi.mlb.com/api/v1/schedule"
STATCAST_DIR = Path(__file__).parent / "data" / "statcast"

SEASON_DATES = {
    2022: ("2022-04-07", "2022-10-05"),
    2023: ("2023-03-30", "2023-10-01"),
    2024: ("2024-03-28", "2024-09-29"),
    2025: ("2025-03-27", "2025-09-28"),
    2026: ("2026-03-26", "2026-09-21"),  # through today
}


def backfill_schedule(year: int):
    start, end = SEASON_DATES[year]
    r = requests.get(SCHED, params={
        "sportId": 1, "startDate": start, "endDate": end,
        "gameType": "R", "hydrate": "probablePitcher",
    }, timeout=60)
    r.raise_for_status()
    con = connect()
    n = 0
    for day in r.json().get("dates", []):
        for g in day.get("games", []):
            if g["status"]["abstractGameState"].lower() != "final":
                continue
            gid = f"mlb-{g['gamePk']}"
            con.execute(
                """INSERT INTO games (game_id, sport, game_date, away, home,
                                      away_starter, home_starter,
                                      away_score, home_score, status)
                   VALUES (?,?,?,?,?,?,?,?,?,'final')
                   ON CONFLICT(game_id) DO UPDATE SET
                     away_score=excluded.away_score,
                     home_score=excluded.home_score, status='final'""",
                (gid, "mlb", day["date"],
                 g["teams"]["away"]["team"]["name"],
                 g["teams"]["home"]["team"]["name"],
                 (g["teams"]["away"].get("probablePitcher") or {}).get("fullName"),
                 (g["teams"]["home"].get("probablePitcher") or {}).get("fullName"),
                 g["teams"]["away"].get("score"), g["teams"]["home"].get("score")))
            n += 1
    con.commit()
    con.close()
    print(f"[{year}] schedule: {n} final games stored.")


def backfill_statcast(year: int):
    out = STATCAST_DIR / f"{year}.parquet"
    if out.exists():
        print(f"[{year}] statcast: already on disk, skipping.")
        return
    from pybaseball import statcast, cache
    cache.enable()
    start, end = SEASON_DATES[year]
    print(f"[{year}] statcast: downloading {start}..{end} (long; go do something else)")
    df = statcast(start_dt=start, end_dt=end)
    keep = ["game_pk", "game_date", "home_team", "away_team", "inning",
            "inning_topbot", "at_bat_number", "pitch_number", "pitcher",
            "batter", "events", "woba_value", "woba_denom"]
    df = df[[c for c in keep if c in df.columns]]
    STATCAST_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out)
    print(f"[{year}] statcast: saved {len(df):,} pitches -> {out.name}")


KEEP_COLS = ["game_pk", "game_date", "home_team", "away_team", "inning",
             "inning_topbot", "at_bat_number", "pitch_number", "pitcher",
             "batter", "events", "woba_value", "woba_denom"]


# How many days back the top-up re-reads on every run. Savant publishes during
# games and revises afterwards, so the most recent days are the unreliable
# ones. Three covers a normal lag; the dedupe keeps the newest copy.
RESETTLE_DAYS = 3


def topup_statcast(year: int | None = None, today: str | None = None,
                   allow_full_download: bool = True) -> int:
    """Append only the days the current season's parquet is missing.

    backfill_statcast() skips any season already on disk, which is right for
    finished seasons and wrong for the live one: the file would sit at whatever
    date it was first written while the rolling windows silently aged. A
    30-day window over data that stops a month ago is not a 30-day window, and
    it produces confident features from nothing recent.

    Returns the number of new pitches appended.
    """
    import pandas as pd
    year = year or dt.date.today().year
    if year not in SEASON_DATES:
        print(f"[{year}] no season dates configured; skipping top-up.")
        return 0
    out = STATCAST_DIR / f"{year}.parquet"
    if not out.exists():
        if not allow_full_download:
            # The cloud runner has no data/ directory (it is gitignored), and a
            # full season is hundreds of MB over 30-60 minutes. Never start one
            # from inside the daily pipeline.
            print(f"[{year}] no parquet on disk and full download not allowed; "
                  f"skipping.")
            return 0
        backfill_statcast(year)
        return 0

    df = pd.read_parquet(out)
    last = pd.to_datetime(df["game_date"]).max().date()
    target = dt.date.fromisoformat(today) if today else dt.date.today()
    season_end = dt.date.fromisoformat(SEASON_DATES[year][1])
    if year == dt.date.today().year:
        # The current season's end date in SEASON_DATES is a frozen "today"
        # from whenever backfill was written, so it caps the top-up at a date
        # already in the past. Statcast returns nothing for days with no games,
        # so asking past the real season end is harmless.
        season_end = max(season_end, target)
    # Stop at YESTERDAY, and always re-read the last few days.
    #
    # The old version fetched last_date+1 .. TODAY, then next time started at
    # today+1. Whatever Savant had not published by the moment it ran was
    # therefore frozen out permanently. Measured on this repo: 2026-09-21 had
    # 3 of the 5 games actually played, and those 2 games could never be
    # collected again.
    #
    # The audit predicted the damage would be truncated games (missing late
    # innings, i.e. bullpen data). On this data it is whole games missing
    # instead - same cause, and a pitch-count check would not have seen it.
    #
    # Two changes: never ask for today, because a day being played is a day
    # still being written; and re-fetch RESETTLE_DAYS back, because Savant
    # revises. Dedupe keeps the last copy, so re-reading is free of charge
    # beyond the download.
    end = min(target - dt.timedelta(days=1), season_end)
    start = max(last - dt.timedelta(days=RESETTLE_DAYS - 1),
                dt.date.fromisoformat(SEASON_DATES[year][0]))
    if start > end:
        print(f"[{year}] statcast current through {last}; nothing to add "
              f"(top-ups stop at yesterday).")
        return 0

    from pybaseball import statcast, cache
    cache.enable()
    print(f"[{year}] statcast top-up: {start} .. {end}")
    new = statcast(start_dt=str(start), end_dt=str(end))
    if new is None or new.empty:
        print(f"[{year}] no new pitches returned (off day, or not posted yet).")
        return 0
    new = new[[c for c in KEEP_COLS if c in new.columns]]

    combined = pd.concat([df, new], ignore_index=True)
    before = len(combined)
    combined = combined.drop_duplicates(
        subset=["game_pk", "at_bat_number", "pitch_number"], keep="last")
    combined["game_date"] = pd.to_datetime(combined["game_date"])
    combined = combined.sort_values(["game_date", "game_pk", "at_bat_number",
                                     "pitch_number"])
    added = len(combined) - len(df)
    # Write game_date back as an ISO STRING, the way backfill_statcast() wrote
    # it. Leaving it as datetime64 silently gave a topped-up season a different
    # dtype from every untouched one, so comparing across files raised
    # "'>' not supported between Timestamp and str". load_statcast() coerces on
    # read and never noticed; audit.py, which compares the files directly, did.
    # A season's dtype must not depend on whether it has been topped up.
    combined["game_date"] = combined["game_date"].dt.strftime("%Y-%m-%d")
    combined.to_parquet(out)
    print(f"[{year}] +{added:,} pitches ({before - len(combined):,} duplicates "
          f"dropped). Now through "
          f"{pd.to_datetime(combined['game_date']).max().date()}.")
    return added


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "topup":
        raise SystemExit(0 if topup_statcast() >= 0 else 1)
    years = (range(int(sys.argv[1]), int(sys.argv[2]) + 1)
             if len(sys.argv) == 3 else SEASON_DATES.keys())
    for y in years:
        backfill_schedule(y)
    for y in years:
        backfill_statcast(y)
    print("Backfill complete. Next: python features/build_training.py")
