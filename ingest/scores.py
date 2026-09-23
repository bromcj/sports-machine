"""Schedule + finals for any sport via ESPN's public scoreboard endpoint.

Keyless and covers NFL/NBA/NHL/NCAA; MLB keeps ingest/mlb.py as the primary
source (probable pitchers), with this as a scores fallback.
"""
import datetime as dt
import requests
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
from db import connect
from ingest import http, quality
from feeds import et_date
from config import SPORTS, active_sports

URL = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"


def pull_sport(sport: str, date: str | None = None) -> int:
    cfg = SPORTS[sport]
    params = {"dates": date.replace("-", "")} if date else {}
    r = http.get(URL.format(path=cfg["espn"]), params=params, label=sport)
    con = connect()
    n = 0
    bad = quality.Rejects(sport)
    for ev in r.json().get("events", []):
        comp = ev["competitions"][0]
        teams = {c["homeAway"]: c for c in comp["competitors"]}
        home, away = teams["home"], teams["away"]
        gid = f"{sport}-espn-{ev['id']}"
        status = comp["status"]["type"]["state"]  # pre | in | post
        away_name = away["team"]["displayName"]
        home_name = home["team"]["displayName"]
        # ev["date"] is the UTC instant. Its first 10 characters are the UTC
        # date, which is a day ahead for anything starting after 8pm ET.
        start_utc = ev.get("date")
        local_date = et_date(start_utc) or ev["date"][:10]
        away_pts = int(away.get("score") or 0) if status != "pre" else None
        home_pts = int(home.get("score") or 0) if status != "pre" else None
        if not bad.check(quality.game(away_name, home_name, local_date,
                                      away_pts, home_pts)):
            continue
        con.execute(
            # 'final' is terminal, and a NULL never overwrites a real score.
            #
            # The previous version assigned excluded.* unconditionally, so when
            # ESPN returned this game as 'pre' - which it does for
            # postponements, re-keyed doubleheaders, and the occasional glitch -
            # a completed 7-2 was silently rewritten to NULL-NULL/scheduled, and
            # the result was gone. merge_archive.py already guarded against
            # exactly this; the primary ingest path did not.
            """INSERT INTO games (game_id, sport, game_date, start_time_utc,
                                  away, home, away_score, home_score, status)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(game_id) DO UPDATE SET
                 start_time_utc=COALESCE(excluded.start_time_utc,
                                         games.start_time_utc),
                 -- A game that has not finished may still be moved.
                 game_date=CASE WHEN games.status='final' THEN games.game_date
                                ELSE excluded.game_date END,
                 away_score=CASE WHEN games.status='final' THEN games.away_score
                            ELSE COALESCE(excluded.away_score, games.away_score) END,
                 home_score=CASE WHEN games.status='final' THEN games.home_score
                            ELSE COALESCE(excluded.home_score, games.home_score) END,
                 status=CASE WHEN games.status='final' THEN 'final'
                             ELSE excluded.status END""",
            (gid, sport, local_date, start_utc, away_name, home_name,
             away_pts, home_pts,
             {"pre": "scheduled", "in": "live", "post": "final"}[status]))
        n += 1
    con.commit()
    con.close()
    print(f"[{sport}] upserted {n} games.")
    bad.report()
    return n


def pull_all(date: str | None = None):
    month = dt.date.today().month
    for sport in active_sports(month):
        try:
            pull_sport(sport, date)
        except requests.RequestException as e:
            print(f"[{sport}] scoreboard failed: {e}")


if __name__ == "__main__":
    pull_all(sys.argv[1] if len(sys.argv) > 1 else None)
