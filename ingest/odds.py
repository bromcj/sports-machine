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
import requests
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
from db import connect, utc_now
from ingest import http, quality
from ingest.http import redact
from ingest.raw import save_raw
from feeds import et_date, et_today
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
    bad = quality.Rejects(sport)
    for ev in r.json():
        gid = f"{sport}-{ev['id']}"
        home, away = ev["home_team"], ev["away_team"]
        commence = ev["commence_time"]          # full ISO timestamp, not just the date
        # commence[:10] is the UTC date. A 10:11pm ET first pitch is 02:11 UTC
        # the NEXT day, so dating by UTC pushed every late game forward a day
        # and matched it to the previous night's game. game_date is local.
        local_date = et_date(commence) or commence[:10]
        if not bad.check(quality.game(away, home, local_date)):
            continue
        con.execute(
            "INSERT INTO games (game_id, sport, game_date, start_time_utc, away, home)"
            " VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(game_id) DO UPDATE SET"
            "   start_time_utc=excluded.start_time_utc,"
            "   game_date=CASE WHEN games.status='final' THEN games.game_date"
            "                  ELSE excluded.game_date END",
            (gid, sport, local_date, commence, away, home))
        for bk in ev.get("bookmakers", []):
            for mkt in bk.get("markets", []):
                if mkt["key"] != "h2h":
                    continue
                prices = {o["name"]: o["price"] for o in mkt["outcomes"]}
                # When the BOOK last moved this price, not when we pulled it.
                # Without it a line that has not moved in six hours looks
                # identical to one that just changed.
                last_up = mkt.get("last_update") or bk.get("last_update")
                if not bad.check(quality.snapshot(prices.get(away),
                                                  prices.get(home))):
                    continue
                # OR IGNORE: ux_snap_dedupe makes a repeat of the same price
                # from the same book in the same pull a no-op rather than an
                # IntegrityError that would abort the run.
                con.execute(
                    "INSERT OR IGNORE INTO odds_snapshots (game_id, sport, ts,"
                    " book, away_ml, home_ml, snapshot_type, commence_time,"
                    " market_last_update) VALUES (?,?,?,?,?,?,?,?,?)",
                    (gid, sport, ts, bk["key"], prices.get(away), prices.get(home),
                     snapshot_type, commence, last_up))
                n += 1
    con.commit()
    con.close()
    remaining = r.headers.get("x-requests-remaining", "?")
    used = r.headers.get("x-requests-used")
    # Logged, not just printed. The console scrolls away and the only record
    # of how fast the budget is going is gone with it.
    con2 = connect()
    con2.execute(
        "INSERT INTO api_usage (ts, sport, endpoint, remaining, used)"
        " VALUES (?,?,?,?,?)",
        (utc_now(), sport, "odds/h2h",
         int(remaining) if str(remaining).isdigit() else None,
         int(used) if used and str(used).isdigit() else None))
    con2.commit()
    con2.close()
    save_raw("odds", sport, r.text)
    print(f"[{sport}] {n} {snapshot_type} snapshots. Credits left: {remaining}")
    bad.report()
    return n


def pull(snapshot_type: str = "open") -> list:
    """Pull every in-season sport. Returns the sports whose pull FAILED.

    A failure is caught so the other sports still get their prices, but it is
    returned, not just printed: a 401 or 429 used to leave the cloud run
    green, with healthcheck reading the empty slate as "no games today".
    """
    if not API_KEY:
        raise SystemExit("Set ODDS_API_KEY env var first.")
    month = et_today().month
    failed = []
    for sport in active_sports(month):
        try:
            pull_sport(sport, snapshot_type)
        except requests.RequestException as e:
            print(f"[{sport}] pull failed: {redact(e)}")
            failed.append(sport)
    return failed


if __name__ == "__main__":
    bad = pull(sys.argv[1] if len(sys.argv) > 1 else "open")
    raise SystemExit(f"odds pull failed for: {', '.join(bad)}" if bad else 0)
