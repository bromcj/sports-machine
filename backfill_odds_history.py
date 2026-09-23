"""One-time paid backfill of historical closing lines, so gate 1 can be real.

    python backfill_odds_history.py                 DRY RUN (default)
    python backfill_odds_history.py --plan          per-season cost breakdown
    python backfill_odds_history.py --execute --max-credits 45000

NOTHING IS PURCHASED OR PULLED BY DEFAULT. --dry-run is the default and
--execute must be passed explicitly, together with a credit cap it cannot
exceed.

WHY THIS EXISTS

Gate 1 asks whether the model beats a REAL de-vigged market out of sample.
MLB has never sat that test: free tiers carry no historical odds, so the
baseline is a placeholder (the home win rate of prior seasons), and beating
a placeholder clears nothing however large the margin. This is the one
purchase that turns gate 1 from a formality into a measurement.

WHAT IT REQUESTS

Only the TEST seasons. walk_forward trains on seasons[:i] and tests
seasons[i], and novig_home_prob is read ONLY for the test season - training
never touches it. So 2022 and 2023 need no odds at all, which removes 18,750
credits from the obvious plan before anything else is decided.

For each date it places one request 45 minutes before each cluster of first
pitches, plus one morning "open" request. A single historical request returns
EVERY game in that snapshot, so games starting together share one request.

Cluster width is 15 minutes, and that number is not arbitrary. A request at
T serves games starting in (T, T+60], because a price more than 60 minutes
before first pitch is not a closing line - that is the same
CLOSING_WINDOW_MIN the rest of the system uses. With the snapshot placed 45
minutes before the cluster anchor, a 15-minute cluster keeps every game
inside that window; a 60-minute cluster puts 39% of games outside it.

    cluster   credits   median lead   within 60 min
      15m      41,210       45 min        100%
      30m      34,380       45 min         78%
      60m      27,410       50 min         61%

SAFETY

  - --dry-run is the default and prints the plan without touching the network
  - --max-credits is a hard cap; x-requests-remaining is read after EVERY
    response and the run stops the moment the budget or the cap is reached
  - resumable: every completed snapshot is recorded, and a rerun skips it
  - raw gzipped responses under data/raw/odds_history/, so a parsing question
    can be answered later without paying twice
  - rows land as snapshot_type 'hist_open' / 'hist_close', which can never be
    confused with a live pull
  - games are matched on START TIME via feeds.py, never on date strings
"""
import argparse
import datetime as dt
import gzip
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
import paths
from db import connect, utc_now
from feeds import SQL_STATS_API, et_date, parse_utc
from ingest.http import redact

HIST_URL = "https://api.the-odds-api.com/v4/historical/sports/{key}/odds"
SPORT_KEY = "baseball_mlb"
BOOKS = "pinnacle,draftkings,fanduel,betmgm"   # one REGION, so still 10 credits
MARKET = "h2h"
CREDITS_PER_REQUEST = 10

# Minutes before a cluster's first pitch to take the snapshot.
OFFSET_MIN = 45
# Cluster width. See the module docstring: 45 + 15 = 60, which is
# CLOSING_WINDOW_MIN, so every game's price is a closing line.
CLUSTER_MIN = 15
# Morning snapshot, in ET, for the "where a bettor actually gets in" report.
OPEN_HOUR_ET = 10

TEST_SEASONS = ("2024", "2025", "2026")
RAW_DIR = paths.RAW_DIR / "odds_history"


def _clusters(starts, tol_min=CLUSTER_MIN):
    """Snapshot times covering every start, each within OFFSET..OFFSET+tol."""
    out, anchor = [], None
    for t in sorted(starts):
        if anchor is None or (t - anchor).total_seconds() / 60 > tol_min:
            anchor = t
            out.append(anchor - dt.timedelta(minutes=OFFSET_MIN))
    return out


def build_plan(seasons=TEST_SEASONS) -> list[dict]:
    """Every snapshot to request. Reads the database; no network."""
    con = connect()
    rows = con.execute(
        f"SELECT game_date, start_time_utc FROM games WHERE sport='mlb'"
        f" AND ({SQL_STATS_API}) AND status='final'"
        f" AND start_time_utc IS NOT NULL ORDER BY game_date").fetchall()
    con.close()
    by_day = defaultdict(list)
    for r in rows:
        if r["game_date"][:4] not in seasons:
            continue
        t = parse_utc(r["start_time_utc"])
        if t:
            by_day[r["game_date"]].append(t)

    plan = []
    for day in sorted(by_day):
        # One morning snapshot: 10am ET on the game's own local date.
        first = min(by_day[day])
        morning = first.replace(hour=14, minute=0, second=0, microsecond=0)
        while morning >= first:
            morning -= dt.timedelta(days=1)
        plan.append({"ts": morning, "kind": "hist_open", "date": day})
        for t in _clusters(by_day[day]):
            plan.append({"ts": t, "kind": "hist_close", "date": day})
    return plan


def _done(con) -> set:
    return {r[0] for r in con.execute(
        "SELECT snapshot_ts FROM odds_history_progress WHERE ok=1")}


def fetch_one(api_key: str, when: dt.datetime):
    """One historical snapshot. Returns (payload, remaining, raw_text)."""
    from ingest import http
    r = http.get(HIST_URL.format(key=SPORT_KEY), params={
        "apiKey": api_key, "bookmakers": BOOKS, "markets": MARKET,
        "oddsFormat": "american",
        "date": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, label="hist")
    rem = r.headers.get("x-requests-remaining")
    return r.json(), (int(rem) if rem and str(rem).isdigit() else None), r.text


def _store(con, payload, kind: str, snapshot_ts: str) -> int:
    """Write events and prices. Same shape as a live pull, different type."""
    from ingest import quality
    from ingest.http import redact as _r   # noqa: F401  (kept for symmetry)
    n = 0
    bad = quality.Rejects("hist")
    for ev in payload.get("data", []) or []:
        gid = f"mlb-{ev['id']}"
        home, away = ev.get("home_team"), ev.get("away_team")
        commence = ev.get("commence_time")
        if not commence or not bad.check(
                quality.game(away, home, et_date(commence) or commence[:10])):
            continue
        con.execute(
            "INSERT INTO games (game_id, sport, game_date, start_time_utc,"
            " away, home) VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(game_id) DO UPDATE SET"
            "   start_time_utc=COALESCE(excluded.start_time_utc,"
            "                           games.start_time_utc)",
            (gid, "mlb", et_date(commence), commence, away, home))
        for bk in ev.get("bookmakers", []) or []:
            for mkt in bk.get("markets", []) or []:
                if mkt.get("key") != MARKET:
                    continue
                px = {o["name"]: o["price"] for o in mkt.get("outcomes", [])}
                if bad.check(quality.snapshot(px.get(away), px.get(home))):
                    con.execute(
                        "INSERT OR IGNORE INTO odds_snapshots (game_id, sport,"
                        " ts, book, away_ml, home_ml, snapshot_type,"
                        " commence_time, market_last_update)"
                        " VALUES (?,?,?,?,?,?,?,?,?)",
                        (gid, "mlb", snapshot_ts, bk["key"], px.get(away),
                         px.get(home), kind, commence, mkt.get("last_update")))
                    n += 1
    bad.report()
    return n


def execute(max_credits: int, seasons=TEST_SEASONS, limit=None):
    import os
    api_key = os.environ.get("ODDS_API_KEY", "")
    if not api_key:
        raise SystemExit("ODDS_API_KEY is not set.")
    plan = build_plan(seasons)
    con = connect()
    done = _done(con)
    todo = [p for p in plan
            if p["ts"].strftime("%Y-%m-%dT%H:%M:%SZ") not in done]
    print(f"{len(plan):,} snapshots planned, {len(done):,} already fetched, "
          f"{len(todo):,} to go.")
    if limit:
        todo = todo[:limit]
        print(f"  --limit {limit}: stopping after {len(todo)}.")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    spent = 0
    for i, item in enumerate(todo, 1):
        if spent + CREDITS_PER_REQUEST > max_credits:
            print(f"\nSTOPPED: the next request would exceed --max-credits "
                  f"({max_credits:,}). Spent {spent:,}.")
            break
        ts = item["ts"].strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            payload, remaining, raw = fetch_one(api_key, item["ts"])
        except Exception as e:
            con.execute(
                "INSERT OR REPLACE INTO odds_history_progress (snapshot_ts,"
                " kind, fetched_at, n_prices, credits_remaining, ok, note)"
                " VALUES (?,?,?,?,?,0,?)",
                (ts, item["kind"], utc_now(), 0, None, redact(e)[:200]))
            con.commit()
            print(f"  [{i}/{len(todo)}] {ts} FAILED: {redact(e)[:90]}")
            continue
        spent += CREDITS_PER_REQUEST
        with gzip.open(RAW_DIR / f"{ts.replace(':', '')}.json.gz", "wt",
                       encoding="utf-8") as f:
            f.write(raw)
        n = _store(con, payload, item["kind"], ts)
        con.execute(
            "INSERT OR REPLACE INTO odds_history_progress (snapshot_ts, kind,"
            " fetched_at, n_prices, credits_remaining, ok, note)"
            " VALUES (?,?,?,?,?,1,NULL)",
            (ts, item["kind"], utc_now(), n, remaining))
        con.commit()
        if i % 25 == 0 or i == len(todo):
            print(f"  [{i}/{len(todo)}] {ts}  +{n} prices  "
                  f"spent {spent:,}  remaining {remaining}")
        # The account's own budget, not just our cap.
        if remaining is not None and remaining < CREDITS_PER_REQUEST:
            print(f"\nSTOPPED: the account has {remaining} credits left.")
            break
    con.close()
    print(f"\nDone. Credits spent this run: {spent:,}")


# /v4/sports is the one endpoint that does NOT count against the quota, and it
# still returns the quota headers. So the plan can be verified as live, and the
# balance checked, without spending anything.
SPORTS_URL = "https://api.the-odds-api.com/v4/sports"


def preflight(seasons=TEST_SEASONS) -> dict:
    """Check the balance against the plan. Costs ZERO credits."""
    import os
    from ingest import http
    api_key = os.environ.get("ODDS_API_KEY", "")
    if not api_key:
        raise SystemExit("ODDS_API_KEY is not set.")
    r = http.get(SPORTS_URL, params={"apiKey": api_key}, label="preflight")
    rem = r.headers.get("x-requests-remaining")
    used = r.headers.get("x-requests-used")
    last = r.headers.get("x-requests-last")
    rem_i = int(rem) if rem and str(rem).isdigit() else None
    used_i = int(used) if used and str(used).isdigit() else None

    con = connect()
    try:
        done = len(_done(con))
    except Exception:
        done = 0
    con.close()
    need = (len(build_plan(seasons)) - done) * CREDITS_PER_REQUEST

    print("PREFLIGHT - this call is free (/v4/sports does not count against "
          "the quota).\n")
    print(f"  credits remaining : {rem if rem else 'unknown'}")
    print(f"  credits used      : {used if used else 'unknown'}")
    print(f"  cost of last call : {last} (0 confirms this one was free)")
    print(f"  plan needs        : {need:,}")
    if rem_i is None:
        print("\n  Could not read the balance. Not safe to run.")
        ok = False
    elif rem_i >= need:
        print(f"\n  ENOUGH: {rem_i - need:,} credits would be left over.")
        ok = True
    else:
        print(f"\n  NOT ENOUGH: short by {need - rem_i:,}. The run would stop "
              f"partway, which is safe - it is resumable - but do not start "
              f"until the upgrade has landed.")
        ok = False
    if rem_i is not None and used_i is not None:
        print(f"  (plan size looks like {rem_i + used_i:,} credits/month)")
    return {"remaining": rem_i, "used": used_i, "need": need, "ok": ok}


def dry_run(seasons=TEST_SEASONS, show=8):
    plan = build_plan(seasons)
    con = connect()
    try:
        done = _done(con)
    except Exception:
        done = set()
    con.close()
    per = defaultdict(lambda: [0, 0])
    for p in plan:
        per[p["date"][:4]][0 if p["kind"] == "hist_open" else 1] += 1

    print("DRY RUN - nothing was requested and nothing was spent.\n")
    print(f"{'season':>7s} {'open':>6s} {'close':>7s} {'requests':>9s} {'credits':>9s}")
    tot = 0
    for s in sorted(per):
        o, c = per[s]
        cr = (o + c) * CREDITS_PER_REQUEST
        tot += cr
        print(f"{s:>7s} {o:6,d} {c:7,d} {o + c:9,d} {cr:9,d}")
    print(f"{'TOTAL':>7s} {'':6s} {'':7s} {len(plan):9,d} {tot:9,d}")
    print(f"\n  already fetched: {len(done):,}   still to fetch: "
          f"{len(plan) - len(done):,}")
    print(f"  books: {BOOKS}   market: {MARKET}   "
          f"({CREDITS_PER_REQUEST} credits per request)")
    print(f"\nFirst {show} requests:")
    for p in plan[:show]:
        print(f"  {p['ts'].strftime('%Y-%m-%dT%H:%M:%SZ')}  {p['kind']:11s} "
              f"(game date {p['date']})")
    print("\nTo run it for real you must pass BOTH --execute and --max-credits.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true",
                    help="actually spend credits (default is a dry run)")
    ap.add_argument("--max-credits", type=int,
                    help="hard cap; required with --execute")
    ap.add_argument("--limit", type=int, help="stop after N requests")
    ap.add_argument("--plan", action="store_true", help="cost breakdown only")
    ap.add_argument("--preflight", action="store_true",
                    help="check the credit balance against the plan (free)")
    a = ap.parse_args()
    if a.preflight:
        raise SystemExit(0 if preflight()["ok"] else 1)
    if a.execute:
        if not a.max_credits:
            raise SystemExit("--execute requires --max-credits.")
        execute(a.max_credits, limit=a.limit)
    else:
        dry_run()
