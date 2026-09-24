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
import paths
from db import connect
from feeds import stats_api_status
from ingest import quality

SCHED = "https://statsapi.mlb.com/api/v1/schedule"
STATCAST_DIR = paths.STATCAST_DIR

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
            # Through feeds.stats_api_status, like every other ingest path:
            # abstractGameState is "Final" for a POSTPONED game too (a raw
            # response for gamePk 824785 shows Final / Postponed, no score).
            if stats_api_status(g.get("status") or {}) != "final":
                continue
            away_score = g["teams"]["away"].get("score")
            home_score = g["teams"]["home"].get("score")
            if quality.game(g["teams"]["away"]["team"]["name"],
                            g["teams"]["home"]["team"]["name"], day["date"],
                            away_score, home_score, "final") is not None:
                continue
            gid = f"mlb-{g['gamePk']}"
            con.execute(
                # Final is terminal and a NULL never overwrites a real value -
                # the rule every other writer follows. This one used to assign
                # the incoming scores unconditionally.
                """INSERT INTO games (game_id, sport, game_date, away, home,
                                      away_starter, home_starter,
                                      away_score, home_score, status)
                   VALUES (?,?,?,?,?,?,?,?,?,'final')
                   ON CONFLICT(game_id) DO UPDATE SET
                     away_score=CASE WHEN games.status='final'
                                     THEN games.away_score
                                     ELSE COALESCE(excluded.away_score,
                                                   games.away_score) END,
                     home_score=CASE WHEN games.status='final'
                                     THEN games.home_score
                                     ELSE COALESCE(excluded.home_score,
                                                   games.home_score) END,
                     status='final'""",
                (gid, "mlb", day["date"],
                 g["teams"]["away"]["team"]["name"],
                 g["teams"]["home"]["team"]["name"],
                 (g["teams"]["away"].get("probablePitcher") or {}).get("fullName"),
                 (g["teams"]["home"].get("probablePitcher") or {}).get("fullName"),
                 away_score, home_score))
            n += 1
    con.commit()
    con.close()
    print(f"[{year}] schedule: {n} final games stored.")


def backfill_venues(year: int) -> int:
    """Fill venue_id and start_time_utc on games already stored. Free.

    Park factors were keyed on a hand-maintained team->park map that knew only
    about the Athletics, so Tampa Bay's 2025 home games at Steinbrenner Field
    were given Tropicana Field's factor, and any neutral-site game got the
    "home" team's park. The Stats API has known the real venue all along.

    Deliberately narrow: it writes ONLY those two columns and never touches
    scores, status or dates. Re-runnable.
    """
    import requests as _rq
    start, end = SEASON_DATES[year]
    r = _rq.get(SCHED, params={"sportId": 1, "startDate": start, "endDate": end,
                               "gameType": "R"}, timeout=90)
    r.raise_for_status()
    con = connect()
    n = 0
    for day in r.json().get("dates", []):
        for g in day.get("games", []):
            venue = (g.get("venue") or {}).get("id")
            if venue is None:
                continue
            n += con.execute(
                "UPDATE games SET venue_id=COALESCE(venue_id, ?),"
                " start_time_utc=COALESCE(start_time_utc, ?) WHERE game_id=?",
                (str(venue), g.get("gameDate"), f"mlb-{g['gamePk']}")).rowcount
    con.commit()
    con.close()
    print(f"[{year}] venues: {n} games updated.")
    return n


def backfill_start_times(year: int) -> int:
    """Correct start_time_utc to the time a game was ACTUALLY played. Free.

    The Stats API returns a postponed game TWICE under the same gamePk: once
    in its original slot with detailedState "Postponed", and again on the
    make-up date as "Final". Both report abstractGameState "Final", so code
    that trusted the abstract field stored the ORIGINAL slot's gameDate
    alongside the rescheduled game's score.

    Measured on 2024-03-28: Brewers @ Mets was stored starting 17:10Z on the
    28th when it was played at 17:40Z on the 29th - twenty hours out. The odds
    feed has the real time, so the two could never be matched, and the
    historical backfill would have aimed a paid request at a moment when the
    game was not on the board.

    Keeps the FINAL entry for each gamePk, and only ever moves a start time.
    """
    import requests as _rq
    from feeds import et_date, stats_api_status
    start, end = SEASON_DATES[year]
    r = _rq.get(SCHED, params={"sportId": 1, "startDate": start, "endDate": end,
                               "gameType": "R"}, timeout=90)
    r.raise_for_status()
    best = {}
    for day in r.json().get("dates", []):
        for g in day.get("games", []):
            status = stats_api_status(g.get("status") or {})
            gid = f"mlb-{g['gamePk']}"
            # A real completion always wins over the postponed placeholder.
            if gid not in best or (status == "final" and best[gid][1] != "final"):
                best[gid] = (g.get("gameDate"), status)

    con = connect()
    moved = 0
    for gid, (when, status) in best.items():
        if not when or status != "final":
            continue
        cur = con.execute(
            "SELECT start_time_utc FROM games WHERE game_id=?", (gid,)).fetchone()
        if cur is None or cur["start_time_utc"] == when:
            continue
        con.execute(
            "UPDATE games SET start_time_utc=?, game_date=? WHERE game_id=?",
            (when, et_date(when), gid))
        moved += 1
    con.commit()
    con.close()
    print(f"[{year}] start times: {moved} game(s) corrected to when they were "
          f"actually played.")
    return moved


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


def topup_window(last, target, year: int, current_year: int):
    """(start, end) dates for a Statcast top-up. See topup_statcast()."""
    season_end = dt.date.fromisoformat(SEASON_DATES[year][1])
    if year == current_year:
        # The current season's end date in SEASON_DATES is a frozen "today"
        # from whenever backfill was written, so it caps the top-up at a date
        # already in the past. Statcast returns nothing for days with no games,
        # so asking past the real season end is harmless.
        season_end = max(season_end, target)
    end = min(target - dt.timedelta(days=1), season_end)
    start = max(last - dt.timedelta(days=RESETTLE_DAYS - 1),
                dt.date.fromisoformat(SEASON_DATES[year][0]))
    return start, end


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
    start, end = topup_window(last, target, year, dt.date.today().year)
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
        # The schedule writes no start time or venue. Without these a rebuilt
        # database cannot match a single game to its odds (feeds.odds_twin
        # needs start_time_utc) and serves every park the static prior. Both
        # are free Stats API calls and safe to re-run.
        backfill_venues(y)
        backfill_start_times(y)
    for y in years:
        backfill_statcast(y)
    print("Backfill complete. Next: python features/build_training.py")
