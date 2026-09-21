"""Pull schedule, probable pitchers, and final scores from the MLB Stats API.

Free, no key. Educational/non-commercial use per MLBAM terms.
"""
import datetime as dt
import requests
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
from db import connect

SCHED = "https://statsapi.mlb.com/api/v1/schedule"


def pull_day(date: str | None = None):
    date = date or dt.date.today().isoformat()
    r = requests.get(SCHED, params={
        "sportId": 1, "date": date,
        "hydrate": "probablePitcher,linescore",
    }, timeout=30)
    r.raise_for_status()
    con = connect()
    n = 0
    for day in r.json().get("dates", []):
        for g in day.get("games", []):
            gid = f"mlb-{g['gamePk']}"
            away = g["teams"]["away"]["team"]["name"]
            home = g["teams"]["home"]["team"]["name"]
            away_sp = (g["teams"]["away"].get("probablePitcher") or {}).get("fullName")
            home_sp = (g["teams"]["home"].get("probablePitcher") or {}).get("fullName")
            away_score = g["teams"]["away"].get("score")
            home_score = g["teams"]["home"].get("score")
            status = g["status"]["abstractGameState"].lower()  # preview/live/final
            con.execute(
                """INSERT INTO games (game_id, sport, game_date, away, home,
                                      away_starter, home_starter,
                                      away_score, home_score, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(game_id) DO UPDATE SET
                     away_starter=excluded.away_starter, home_starter=excluded.home_starter,
                     away_score=excluded.away_score, home_score=excluded.home_score,
                     status=excluded.status""",
                (gid, "mlb", date, away, home, away_sp, home_sp,
                 away_score, home_score, status),
            )
            n += 1
    con.commit()
    con.close()
    print(f"Upserted {n} games for {date}.")


if __name__ == "__main__":
    pull_day(sys.argv[1] if len(sys.argv) > 1 else None)
