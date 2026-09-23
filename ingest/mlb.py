"""Pull schedule, probable pitchers, and final scores from the MLB Stats API.

Free, no key. Educational/non-commercial use per MLBAM terms.
"""
import datetime as dt
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
from db import connect
from ingest import http, quality
from feeds import et_date

SCHED = "https://statsapi.mlb.com/api/v1/schedule"


def pull_day(date: str | None = None):
    date = date or dt.date.today().isoformat()
    r = http.get(SCHED, params={
        "sportId": 1, "date": date,
        "hydrate": "probablePitcher,linescore",
    }, label="mlb")
    con = connect()
    n = 0
    bad = quality.Rejects("mlb")
    for day in r.json().get("dates", []):
        for g in day.get("games", []):
            gid = f"mlb-{g['gamePk']}"
            away = g["teams"]["away"]["team"]["name"]
            home = g["teams"]["home"]["team"]["name"]
            away_pp = g["teams"]["away"].get("probablePitcher") or {}
            home_pp = g["teams"]["home"].get("probablePitcher") or {}
            away_sp, away_sp_id = away_pp.get("fullName"), away_pp.get("id")
            home_sp, home_sp_id = home_pp.get("fullName"), home_pp.get("id")
            away_score = g["teams"]["away"].get("score")
            home_score = g["teams"]["home"].get("score")
            start_utc = g.get("gameDate")          # true first pitch, ISO Z
            venue = ((g.get("venue") or {}).get("id"))
            # The Stats API already dates games locally, so game_date is right.
            # Keep it, and record the instant as well.
            status = g["status"]["abstractGameState"].lower()  # preview/live/final
            if not bad.check(quality.game(away, home, date,
                                          away_score, home_score)):
                continue
            con.execute(
                """INSERT INTO games (game_id, sport, game_date, start_time_utc,
                                      venue_id, away, home,
                                      away_starter, home_starter,
                                      away_starter_id, home_starter_id,
                                      away_score, home_score, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   -- Same rule as ingest/scores.py: 'final' is terminal and a
                   -- NULL never overwrites a real value. The starters matter
                   -- as much as the scores here - the API drops
                   -- probablePitcher from some responses, and an unconditional
                   -- assignment wiped a confirmed starter that features/build.py
                   -- requires before it will predict a game at all.
                   ON CONFLICT(game_id) DO UPDATE SET
                     start_time_utc=COALESCE(excluded.start_time_utc,
                                             games.start_time_utc),
                     venue_id=COALESCE(excluded.venue_id, games.venue_id),
                     away_starter=COALESCE(excluded.away_starter, games.away_starter),
                     home_starter=COALESCE(excluded.home_starter, games.home_starter),
                     away_starter_id=COALESCE(excluded.away_starter_id,
                                              games.away_starter_id),
                     home_starter_id=COALESCE(excluded.home_starter_id,
                                              games.home_starter_id),
                     away_score=CASE WHEN games.status='final' THEN games.away_score
                                ELSE COALESCE(excluded.away_score, games.away_score) END,
                     home_score=CASE WHEN games.status='final' THEN games.home_score
                                ELSE COALESCE(excluded.home_score, games.home_score) END,
                     status=CASE WHEN games.status='final' THEN 'final'
                                 ELSE excluded.status END""",
                (gid, "mlb", date, start_utc, venue, away, home, away_sp, home_sp,
                 away_sp_id, home_sp_id, away_score, home_score, status),
            )
            n += 1
    con.commit()
    con.close()
    print(f"Upserted {n} games for {date}.")
    bad.report()


if __name__ == "__main__":
    pull_day(sys.argv[1] if len(sys.argv) > 1 else None)
