"""Pull moneylines for every active in-season sport from The Odds API.

Snapshot types: open (morning) | bettime (at wager) | close (CLV anchor).
These label WHICH PULL a row came from. Because a single pull returns tonight's
games alongside games days away, the label alone does not mean "near the close" -
each row also stores commence_time, so the true closing snapshot for any one
game is the latest row before its first pitch. See bets.log.closing_snapshot.
Requires ODDS_API_KEY. Each sport pull costs credits — budget accordingly
(4 active sports x 2 snapshots/day fits easily in the $30/mo tier).
"""
import os
import datetime as dt
import requests
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
from db import connect, utc_now
from ingest import http
from config import SPORTS, active_sports

API_KEY = os.environ.get("ODDS_API_KEY", "")
BASE = "https://api.the-odds-api.com/v4/sports/{key}/odds"
BOOKS = "draftkings,fanduel,betmgm,pinnacle"


def pull_sport(sport: str, snapshot_type: str) -> int:
    cfg = SPORTS[sport]
    r = http.get(BASE.format(key=cfg["odds_key"]), params={
        "apiKey": API_KEY, "regions": "us", "markets": "h2h",
        "oddsFormat": "american", "bookmakers": BOOKS,
    }, label=sport)
    ts = utc_now()
    con = connect()
    n = 0
    for ev in r.json():
        gid = f"{sport}-{ev['id']}"
        home, away = ev["home_team"], ev["away_team"]
        commence = ev["commence_time"]          # full ISO timestamp, not just the date
        con.execute(
            "INSERT OR IGNORE INTO games (game_id, sport, game_date, away, home)"
            " VALUES (?,?,?,?,?)",
            (gid, sport, commence[:10], away, home))
        for bk in ev.get("bookmakers", []):
            for mkt in bk.get("markets", []):
                if mkt["key"] != "h2h":
                    continue
                prices = {o["name"]: o["price"] for o in mkt["outcomes"]}
                # OR IGNORE: ux_snap_dedupe makes a repeat of the same price
                # from the same book in the same pull a no-op rather than an
                # IntegrityError that would abort the run.
                con.execute(
                    "INSERT OR IGNORE INTO odds_snapshots (game_id, sport, ts, book,"
                    " away_ml, home_ml, snapshot_type, commence_time)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (gid, sport, ts, bk["key"], prices.get(away), prices.get(home),
                     snapshot_type, commence))
                n += 1
    con.commit()
    con.close()
    remaining = r.headers.get("x-requests-remaining", "?")
    print(f"[{sport}] {n} {snapshot_type} snapshots. Credits left: {remaining}")
    return n


def pull(snapshot_type: str = "open"):
    if not API_KEY:
        raise SystemExit("Set ODDS_API_KEY env var first.")
    month = dt.date.today().month
    for sport in active_sports(month):
        try:
            pull_sport(sport, snapshot_type)
        except requests.RequestException as e:
            print(f"[{sport}] pull failed: {e}")


if __name__ == "__main__":
    pull(sys.argv[1] if len(sys.argv) > 1 else "open")
