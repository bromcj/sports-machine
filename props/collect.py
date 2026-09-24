"""Phase 0: collect live prop and game prices while they are cheap.

    python props/collect.py --plan            # what it would do, free
    python props/collect.py --run             # actually pull
    python props/collect.py --run --sport nba

WHY NOW. A live event-odds request costs 1 credit per market. The same snapshot
bought historically costs 10. Whatever question gets asked in January, the 2026
season is ten times cheaper to record today than to buy back later - and it has
the additional property that nobody has fitted anything on it, which is the only
thing that makes a test in January worth running at all.

WHAT IT COLLECTS. Per sport, at the scheduled times below:

  NFL   player_receptions, player_reception_yds   (per event)
        h2h, spreads                              (one request, whole slate)
  NBA   player_points, player_rebounds, player_assists  (per event)
        h2h                                       (one request, whole slate)

plus the injury report at each pull, timestamped, so "who was ruled out and
when" is known at decision time rather than reconstructed afterwards. B3 lost
its pre-registered feature to exactly that reconstruction: a player ruled out
has no row in the week's player stats, so the join found nothing.

THE BOOK LIST IS NAMED, NEVER `regions=us`. Pinnacle is not US-licensed and
sits in the `eu` region, so a `regions=us` pull returns no sharp price and no
error. Cost is the number of REGIONS the named books span, not the number of
books - so asking for Pinnacle doubles every request, and once doubled the
extra US books are free. See the BOOKS constant for the measurements.

CREDIT CAP, ENFORCED IN CODE. The brief allows 3,000 credits a month. Every
request's cost is logged to requests.jsonl, and a run starts from what this
calendar month has already spent there, then stops before any request that
would take that running total past the cap. The cap is checked BEFORE the
request, not after, because a cap you discover you have passed is not a cap.
(It used to start from zero on every run, so ten scheduled runs a day could
each spend the whole cap.)

NO SCHEMA CHANGE. Raw payloads land under data/props_live/raw/ gzipped, a
JSONL log records every request, and prices are materialised to parquet.
Credits go to the existing api_usage table, which is an insert into a table
that already exists. Phase 1 has not built its golden test yet, and changing
the live schema before there is a way to prove nothing moved would be the
wrong order.
"""
import argparse
import datetime as dt
import gzip
import json
import os
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import paths
from db import connect, utc_now
from feeds import parse_utc
from ingest import http

API = "https://api.the-odds-api.com/v4"
ET = ZoneInfo("America/New_York")
OUT = paths.DATA_DIR / "props_live"
RAW = OUT / "raw"
LOG = OUT / "requests.jsonl"

# Every book the API carries that is either NJ-legal or worth holding as a
# reference. E4's "almost nothing to shop" was measured across four books, and
# the cheapest open question is whether a wider list changes that.
#
# NAMING BOOKS IS NOT QUITE FREE, and the earlier note in this project that it
# was is only true within one region. Measured 2026-09-24, same market, same
# endpoint:
#
#   11 NJ books + Pinnacle       2 credits per market
#    4 offshore books alone      1 credit  per market
#   all 15                       2 credits per market
#
# So the cost is the number of REGIONS the named books span, not the number of
# books. Pinnacle sits in `eu` and every US book in `us`/`us2`, so asking for
# the sharp anchor doubles every request - and once it is doubled, the four
# offshore books cost nothing extra. Pinnacle is not optional: it is the fair
# line the whole scoring path is built on, and B2 exists because getting it
# wrong returns no error. So: pay the two, take all fifteen.
BOOKS = ",".join([
    "pinnacle",                                   # the sharp anchor, eu region
    "draftkings", "fanduel", "betmgm", "williamhill_us", "betrivers",
    "espnbet", "fanatics", "ballybet", "hardrockbet", "betparx",
    "bovada", "betonlineag", "lowvig", "mybookieag",   # reference / offshore
])

SPORTS = {
    "nfl": {
        "key": "americanfootball_nfl",
        "player_markets": ["player_receptions", "player_reception_yds"],
        "game_markets": ["h2h", "spreads"],
        # Each game gets ONE snapshot, taken at the scheduled pull that falls
        # closest before its kickoff. Pulling every upcoming game at every pull
        # would multiply the cost by four for prices that mostly have not moved.
        "lead_hours": 5.0,
    },
    "nba": {
        "key": "basketball_nba",
        "player_markets": ["player_points", "player_rebounds",
                           "player_assists"],
        "game_markets": ["h2h"],
        "lead_hours": 6.0,
    },
}

# Local time, ET. The NFL times are each a couple of hours before a slate;
# Thursday evening is there because B2 measured Pinnacle as NOT having posted
# props by Thursday noon, and 6pm is the first time it plausibly has.
SCHEDULE = {
    "nfl": ["Thu 18:00", "Sat 20:00", "Sun 11:45", "Sun 15:45", "Mon 18:00"],
    "nba": ["Mon-Sun 16:00", "Mon-Sun 21:00"],
}

CAP_DEFAULT = 3000


def _key() -> str:
    """The persisted user key, not whatever this process inherited.

    A session in this project inherited a stale ODDS_API_KEY - the free
    500/month one - because the process started before the paid key was set,
    and spent four credits against the wrong account before anyone noticed.
    """
    if sys.platform == "win32":
        try:
            out = subprocess.run(
                ["reg", "query", r"HKCU\Environment", "/v", "ODDS_API_KEY"],
                capture_output=True, text=True).stdout
            for line in out.splitlines():
                if "REG_SZ" in line:
                    v = line.split("REG_SZ")[-1].strip()
                    if v:
                        return v
        except Exception:
            pass
    k = os.environ.get("ODDS_API_KEY", "")
    if not k:
        raise SystemExit("ODDS_API_KEY is not set.")
    return k


def spent_this_month(now=None) -> int:
    """Credits the request log records for the current UTC calendar month."""
    now = now or dt.datetime.now(dt.timezone.utc)
    month = now.strftime("%Y-%m")
    total = 0
    if not LOG.exists():
        return 0
    with open(LOG, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(r.get("ts", "")).startswith(month):
                total += int(r.get("cost") or 0)
    return total


class Budget:
    """Stops BEFORE the request that would pass the cap.

    `spent` starts at what this month has already cost, so the cap is a
    running total across runs, not a per-run allowance.
    """

    def __init__(self, cap: int, already: int = 0):
        self.cap = cap
        self.spent = already
        self.remaining = None

    def can(self, cost: int) -> bool:
        return self.spent + cost <= self.cap

    def charge(self, resp) -> int:
        last = int(resp.headers.get("x-requests-last") or 0)
        self.spent += last
        rem = resp.headers.get("x-requests-remaining")
        self.remaining = int(rem) if rem is not None else self.remaining
        return last


def _log(rec: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def _usage(endpoint: str, sport: str, resp) -> None:
    try:
        con = connect()
        con.execute(
            "INSERT INTO api_usage (ts, sport, endpoint, remaining, used)"
            " VALUES (?,?,?,?,?)",
            (utc_now(), sport, endpoint,
             int(resp.headers.get("x-requests-remaining") or 0),
             int(resp.headers.get("x-requests-used") or 0)))
        con.commit()
        con.close()
    except Exception:
        pass


def _save(sport: str, kind: str, stamp: str, name: str, payload) -> Path:
    d = RAW / sport / stamp.replace(":", "").replace("-", "")[:13] / kind
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.json.gz"
    with gzip.open(p, "wt", encoding="utf-8") as f:
        json.dump(payload, f)
    return p


# ----------------------------------------------------------------- pulls ---

def upcoming(sport: str, key: str, budget: Budget) -> list:
    """Events list. Free - verified from the header, not from the docs."""
    cfg = SPORTS[sport]
    r = http.get(f"{API}/sports/{cfg['key']}/events",
                 params={"apiKey": key}, label=f"events {sport}")
    budget.charge(r)
    _usage("events", sport, r)
    return r.json()


def due(events: list, lead_hours: float, now=None) -> list:
    """Events kicking off inside the lead window and not already pulled.

    One snapshot per game, taken at the scheduled pull closest before its
    kickoff. A game already recorded at a nearer time is not pulled again.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    done = already_pulled()
    out = []
    for e in events:
        ct = parse_utc(e.get("commence_time"))
        if ct is None:
            continue
        hours = (ct - now).total_seconds() / 3600.0
        if 0 < hours <= lead_hours and e["id"] not in done:
            out.append(e)
    return out


def already_pulled() -> set:
    got = set()
    if not LOG.exists():
        return got
    with open(LOG, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("ok") and r.get("kind") == "player_props":
                got.add(r.get("event_id"))
    return got


def pull_game_markets(sport: str, key: str, budget: Budget, stamp: str) -> int:
    """h2h and spreads for the whole slate: one request, one credit a market."""
    cfg = SPORTS[sport]
    cost = len(cfg["game_markets"]) * REGIONS   # what it will really cost
    if not budget.can(cost):
        print(f"  game markets skipped: cap would be passed")
        return 0
    r = http.get(f"{API}/sports/{cfg['key']}/odds",
                 params={"apiKey": key, "bookmakers": BOOKS,
                         "markets": ",".join(cfg["game_markets"]),
                         "oddsFormat": "american"}, label=f"game {sport}")
    spent = budget.charge(r)
    _usage("odds", sport, r)
    payload = r.json()
    _save(sport, "game", stamp, "slate", payload)
    _log({"ts": utc_now(), "sport": sport, "kind": "game_markets", "ok": True,
          "events": len(payload), "cost": spent,
          "remaining": budget.remaining})
    print(f"  game markets: {len(payload)} events, {spent} credit(s)")
    return spent


def pull_player_props(sport: str, key: str, budget: Budget, stamp: str,
                      events: list) -> int:
    cfg = SPORTS[sport]
    per = len(cfg["player_markets"]) * REGIONS
    spent = 0
    for e in events:
        if not budget.can(per):
            print(f"  STOPPING at the cap: {budget.spent}/{budget.cap}")
            break
        try:
            r = http.get(
                f"{API}/sports/{cfg['key']}/events/{e['id']}/odds",
                params={"apiKey": key, "bookmakers": BOOKS,
                        "markets": ",".join(cfg["player_markets"]),
                        "oddsFormat": "american"}, label=f"props {sport}")
        except Exception as exc:
            _log({"ts": utc_now(), "sport": sport, "kind": "player_props",
                  "ok": False, "event_id": e["id"],
                  "err": http.redact(str(exc))[:200]})
            print(f"    {e['id'][:10]} FAILED: {http.redact(exc)}")
            continue
        cost = budget.charge(r)
        spent += cost
        _usage("event-odds", sport, r)
        payload = r.json()
        _save(sport, "props", stamp, e["id"], payload)
        nb = len(payload.get("bookmakers", []))
        _log({"ts": utc_now(), "sport": sport, "kind": "player_props",
              "ok": True, "event_id": e["id"],
              "commence_time": e.get("commence_time"),
              "away": e.get("away_team"), "home": e.get("home_team"),
              "books": nb, "cost": cost, "remaining": budget.remaining})
        print(f"    {e.get('away_team')} @ {e.get('home_team')}  "
              f"{nb} books, {cost} credit(s)")
    return spent


def snapshot_injuries(stamp: str) -> int:
    """The pre-game injury report, timestamped. Free - nflverse is public.

    Saved as its own dated file rather than merged into a rolling one: the
    question a January test asks is "who was ruled out AT THE TIME OF THE
    PULL", and a table that gets corrected later cannot answer it.
    """
    try:
        from props.nfl_data import fetch
        d = fetch("injuries", seasons=(dt.date.today().year,), refresh=True)
    except Exception as e:
        print(f"  injuries: unavailable ({type(e).__name__})")
        return 0
    p = OUT / "injuries" / f"{stamp.replace(':', '')[:13]}.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    d.to_parquet(p, index=False)
    print(f"  injuries: {len(d):,} rows -> {p.name}")
    return len(d)


# ------------------------------------------------------------------ cost ---

# Measured, not assumed: the named book list spans two regions because
# Pinnacle is in `eu`. Every request therefore costs markets x 2.
REGIONS = 2


def cost_model() -> pd.DataFrame:
    """What the schedule costs per week and per month. No calls."""
    r = REGIONS
    nfl, nba = SPORTS["nfl"], SPORTS["nba"]
    rows = [
        ("NFL player props", 16 * len(nfl["player_markets"]) * r,
         f"16 games x 2 markets x {r} regions, one snapshot each"),
        ("NFL game markets", 5 * len(nfl["game_markets"]) * r,
         f"5 pulls x 2 markets x {r} regions, one request per slate"),
        ("NBA player props", int(7.5 * 7 * len(nba["player_markets"]) * r),
         f"~7.5 games/day x 3 markets x {r} regions, one snapshot each"),
        ("NBA game markets", 14 * len(nba["game_markets"]) * r,
         f"2 pulls/day x 1 market x {r} regions"),
    ]
    d = pd.DataFrame(rows, columns=["stream", "credits_per_week", "basis"])
    d["credits_per_month"] = (d["credits_per_week"] * 52 / 12).round().astype(int)
    return d


def plan(sport: str | None = None) -> None:
    print("=" * 74)
    print("PHASE 0 COLLECTION PLAN  (no calls made)")
    print("=" * 74)
    print(f"\nbooks named ({len(BOOKS.split(','))}): {BOOKS}")
    print("\nschedule (local ET, run from the local job so the timing holds)")
    for s, times in SCHEDULE.items():
        print(f"  {s.upper():4s} {', '.join(times)}")
    d = cost_model()
    print("\ncost")
    print(d.to_string(index=False))
    wk = d["credits_per_week"].sum()
    mo = d["credits_per_month"].sum()
    print(f"\n  per week  {wk:,}")
    print(f"  per month {mo:,}   (cap for this brief: {CAP_DEFAULT:,})")
    nfl_only = d[d["stream"].str.startswith("NFL")]["credits_per_month"].sum()
    print(f"\n  NFL only, until NBA opens in late October: "
          f"{nfl_only:,}/month")
    if mo <= CAP_DEFAULT:
        print("\n  UNDER the cap - the schedule runs as written.")
    else:
        print("\n  OVER the cap - thin it: drop Saturday, then NBA to one "
              "pull a day.")


def run(sport: str, cap: int) -> int:
    budget = Budget(cap, already=spent_this_month())
    stamp = utc_now()
    cfg = SPORTS[sport]
    print("=" * 74)
    print(f"collecting {sport.upper()}  {stamp}  cap {cap:,} a month, "
          f"{budget.spent:,} spent so far this month")
    print("=" * 74)
    if budget.spent >= cap:
        print("  STOPPING: this month's cap is already spent. Nothing requested.")
        return 0
    key = _key()

    evs = upcoming(sport, key, budget)
    d = due(evs, cfg["lead_hours"])
    print(f"  {len(evs)} upcoming, {len(d)} due inside "
          f"{cfg['lead_hours']}h and not yet pulled")

    # Nothing due means nothing is spent, not even on the slate-wide request.
    # The events list is free, so the task can be scheduled several times a day
    # every day and simply do nothing on the days with no games - which is far
    # more robust than seven separate triggers that have to be kept in step
    # with a league calendar.
    if not d:
        print("  nothing due in the window; no credits spent")
        return 0

    pull_game_markets(sport, key, budget, stamp)
    pull_player_props(sport, key, budget, stamp, d)
    if sport == "nfl":
        snapshot_injuries(stamp)

    print(f"\nmonth to date {budget.spent} credit(s)"
          + (f", {budget.remaining:,} remaining" if budget.remaining else ""))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--sport", default="nfl", choices=sorted(SPORTS))
    ap.add_argument("--cap", type=int, default=CAP_DEFAULT)
    ap.add_argument("--lead-hours", type=float, default=None,
                    help="override the per-sport lead window, for a bounded "
                         "test run outside the normal schedule")
    a = ap.parse_args()
    if a.run:
        if a.lead_hours is not None:
            SPORTS[a.sport]["lead_hours"] = a.lead_hours
        raise SystemExit(run(a.sport, a.cap))
    plan(a.sport)
