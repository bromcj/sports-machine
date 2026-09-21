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


if __name__ == "__main__":
    years = (range(int(sys.argv[1]), int(sys.argv[2]) + 1)
             if len(sys.argv) == 3 else SEASON_DATES.keys())
    for y in years:
        backfill_schedule(y)
    for y in years:
        backfill_statcast(y)
    print("Backfill complete. Next: python features/build_training.py")
