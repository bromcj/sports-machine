"""B2: what props actually exist, at which books, and what a backtest costs.

    python props/preflight.py                   # free: cost model only
    python props/preflight.py --probe --max-credits 12

docs/experiments.md B2. Two jobs, and the order matters because the second one
is worth about twenty thousand credits.

  1. FIND OUT WHAT IS THERE. Which books post each prop market, and - the
     question that changes the whole design - does PINNACLE post any of them?
     Every scoring path in this project de-vigs Pinnacle and calls it fair. If
     Pinnacle does not price props, "fair" has to become the de-vigged
     consensus across all US books, and which source was used has to be
     recorded on every single row, or a later comparison silently mixes two
     different definitions of the thing it is comparing.

  2. COST THE BACKTEST BEFORE BUYING IT. Historical event odds cost 10 credits
     per market per region per event. That number multiplies fast and there is
     no undo.

WHAT THIS SPENDS. Nothing, unless --probe is passed. With it, one live event
odds request per sport, which is 1 credit per market per region - about 8
credits total, and it stops at --max-credits. Costs are read from the API's own
x-requests-last header rather than assumed, because assuming is how a 10x
historical multiplier turns into a surprise.

THE CREDIT ARITHMETIC IS COMPUTED FROM OUR OWN GAME COUNTS, not from paid
calls to the historical events endpoint. We already hold every MLB game and
every NFL schedule row we would be pricing, so counting them is free and
exact.

----------------------------------------------------------------------------
WHAT THIS ACTUALLY FOUND, 2026-09-24. Measured, not assumed; 28 credits.

  NFL props are in good shape.     player_receptions, player_reception_yds,
    player_rush_yds and player_pass_yds are all posted, and PINNACLE POSTS
    THEM - live and historically. The brief expected no sharp price on props;
    there is one. Nine books come back for a single credit.

  MLB props are thin.              pitcher_strikeouts exists but only at
    FanDuel, Fanatics and Bovada, with no Pinnacle and no DraftKings or
    BetMGM. pitcher_outs, pitcher_hits_allowed and h2h_1st_5_innings returned
    nothing at all from any book we asked for. So MLB has no sharp anchor and
    a two-or-three-book consensus, which is a much weaker thing to score
    against than the nine-book NFL market.

  Props are pulled at first pitch. Every MLB probe against a game inside its
    final hour returned nothing and cost nothing; the same market on the next
    day's game returned prices. Any live prop collection has to run further
    out than the moneyline pulls do.

  Costs confirmed against the headers, not the docs:
    events list, live            0 credits
    events list, historical      1 credit
    event odds, live             1 credit per market that HAS data
    event odds, historical      10 credits per market  (20 for two, measured)

  Historical props exist in 2023. A 2023-10-15 snapshot returned both
    receiving markets from six books including Pinnacle, which was the single
    biggest risk to a 16,000-credit spend and is now retired.
"""
import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import paths
from db import connect
from feeds import SQL_STATS_API
from ingest import http

API = "https://api.the-odds-api.com/v4"

# NAMED BOOKS, NOT regions=us. Two things, both measured on 2026-09-24:
#
#   1. regions=us does NOT include Pinnacle. Pinnacle is not US-licensed and
#      sits in the `eu` region, so a props pull built on regions=us silently
#      throws away the only sharp price in the response - the exact anchor
#      every scoring path in this project is built on. The brief expected
#      Pinnacle to be absent from props entirely; it is not, it was just being
#      asked for the wrong way.
#   2. A named bookmakers list counts as ONE region however long it is.
#      Verified: 10 named books, 1 market, cost = 1 credit, 9 books returned.
#
# So we get a sharp anchor AND a nine-book shop for the price of one book.
BOOKS = ("pinnacle,draftkings,fanduel,betmgm,betrivers,betonlineag,"
         "bovada,fanatics,espnbet")

# Market keys from the Odds API docs. Game-level markets come from the plain
# /odds endpoint; player markets only exist on the per-EVENT endpoint, which is
# what makes props expensive - one request per game rather than one per slate.
NFL_PLAYER = ["player_receptions", "player_reception_yds", "player_rush_yds",
              "player_pass_yds"]
MLB_PLAYER = ["pitcher_strikeouts", "pitcher_outs", "pitcher_hits_allowed"]
MLB_GAME = ["h2h_1st_5_innings"]

CREDITS_HISTORICAL = 10     # per market, per region, per event
CREDITS_LIVE = 1
CAP = 55_000                # the hard cap in NEXT_TASK.md


def _key() -> str:
    k = os.environ.get("ODDS_API_KEY", "")
    if not k:
        raise SystemExit("ODDS_API_KEY is not set.")
    return k


def _usage(resp) -> dict:
    """What the API says this request cost. Its numbers, not our guess."""
    h = resp.headers
    def _i(name):
        try:
            return int(h.get(name, ""))
        except ValueError:
            return None
    return {"last": _i("x-requests-last"), "used": _i("x-requests-used"),
            "remaining": _i("x-requests-remaining")}


# ------------------------------------------------------------ free calls ---

def list_sports(spent: dict) -> list:
    r = http.get(f"{API}/sports", params={"apiKey": _key()}, label="sports")
    u = _usage(r)
    spent["calls"] += 1
    spent["credits"] += u["last"] or 0
    spent["remaining"] = u["remaining"]
    return r.json()


def list_events(sport_key: str, spent: dict) -> list:
    """The events endpoint is documented as free. Verified from the header."""
    r = http.get(f"{API}/sports/{sport_key}/events",
                 params={"apiKey": _key()}, label=f"events {sport_key}")
    u = _usage(r)
    spent["calls"] += 1
    spent["credits"] += u["last"] or 0
    spent["remaining"] = u["remaining"]
    print(f"  events({sport_key}): {len(r.json())} events, "
          f"cost {u['last']} credit(s)")
    return r.json()


# ------------------------------------------------------------ paid probe ---

def probe_event(sport_key: str, event_id: str, markets: list,
                spent: dict, max_credits: int) -> dict:
    """One live event-odds request. 1 credit per market per region."""
    would = len(markets) * 1
    if spent["credits"] + would > max_credits:
        print(f"  SKIPPED {sport_key} {markets}: would cost {would}, "
              f"budget {max_credits - spent['credits']} left")
        return {}
    r = http.get(f"{API}/sports/{sport_key}/events/{event_id}/odds",
                 params={"apiKey": _key(), "markets": ",".join(markets),
                         "oddsFormat": "american", "bookmakers": BOOKS},
                 label=f"props {sport_key}")
    u = _usage(r)
    spent["calls"] += 1
    spent["credits"] += u["last"] or 0
    spent["remaining"] = u["remaining"]
    print(f"  event odds {sport_key} [{len(markets)} markets]: "
          f"cost {u['last']} credit(s), {u['remaining']} remaining")
    return r.json()


def summarise_books(payload: dict) -> dict:
    """market -> {book: n_outcomes}. The answer to 'who posts this'."""
    found = defaultdict(lambda: defaultdict(int))
    for bk in payload.get("bookmakers", []):
        for mkt in bk.get("markets", []):
            found[mkt["key"]][bk["key"]] += len(mkt.get("outcomes", []))
    return {k: dict(v) for k, v in found.items()}


# ----------------------------------------------------------- cost model ----

def game_counts() -> dict:
    """How many events each candidate backtest would have to price."""
    con = connect()
    mlb = pd.read_sql(
        f"SELECT substr(game_date,1,4) season, substr(game_date,6,2) month,"
        f" COUNT(*) n FROM games WHERE sport='mlb' AND ({SQL_STATS_API})"
        f" AND status='final' GROUP BY 1,2", con)
    con.close()
    out = {"mlb": {}}
    for s, g in mlb.groupby("season"):
        out["mlb"][s] = {"all": int(g["n"].sum()),
                         "second_half": int(g[g["month"] >= "07"]["n"].sum())}

    nfl_p = paths.training_table("nfl")
    out["nfl"] = {}
    if nfl_p.exists():
        nfl = pd.read_parquet(nfl_p)
        for s, g in nfl.groupby(nfl["season"].astype(int)):
            out["nfl"][str(s)] = {"all": int(len(g))}
    return out


def cost(events: int, markets: int, historical: bool = True) -> int:
    per = CREDITS_HISTORICAL if historical else CREDITS_LIVE
    return events * markets * per


def report_costs(counts: dict) -> None:
    print("\n" + "=" * 74)
    print("COST MODEL  (historical event odds = 10 credits per market per "
          "event)")
    print("=" * 74)

    nfl_seasons = ["2023", "2024", "2025"]
    n_nfl = sum(counts["nfl"].get(s, {}).get("all", 0) for s in nfl_seasons)
    if n_nfl == 0:
        # nflverse rows only exist for seasons already built; fall back to the
        # real schedule size rather than silently costing a backtest at zero.
        n_nfl = 272 * len(nfl_seasons)
        print(f"  (no nflverse rows for {nfl_seasons}; using the real "
              f"schedule, 272 games/season)")

    print(f"\nNFL {'/'.join(nfl_seasons)}  -  {n_nfl:,} games")
    for name, mk in (("receptions only", 1),
                     ("receptions + reception yds", 2),
                     ("+ rush yds", 3)):
        print(f"  one snapshot ~60 min before kickoff, {name:26s} "
              f"{cost(n_nfl, mk):>8,} credits")
    n25 = counts["nfl"].get("2025", {}).get("all", 272)
    print(f"  Thursday-noon OPENING snapshot, 2025 only (2 markets, "
          f"{n25:,} games) {cost(n25, 2):>8,} credits")

    print("\n  events-list calls: the historical events endpoint is 1 credit")
    print("  per request, and one request covers every game kicking off in")
    print("  that window - roughly 5 kickoff slots a week.")
    print(f"    ~5 slots x 18 weeks x 3 seasons = ~270 credits")

    print("\nMLB pitcher_strikeouts")
    for season in sorted(counts["mlb"]):
        c = counts["mlb"][season]
        print(f"  {season} full season  {c['all']:>5,} games   "
              f"{cost(c['all'], 1):>8,} credits"
              f"      second half only {c['second_half']:>5,} games   "
              f"{cost(c['second_half'], 1):>7,}")

    print("\n" + "-" * 74)
    print("ALLOCATION under the 55,000 cap, NFL first (it is in season now;")
    print("MLB props cannot be paper-traded until March)")
    print("-" * 74)
    nfl_2mk = cost(n_nfl, 2) + 270
    nfl_open = cost(n25, 2) + 20
    mlb_25 = cost(counts["mlb"].get("2025", {}).get("all", 2430), 1) + 200
    mlb_h2 = cost(counts["mlb"].get("2025", {}).get("second_half", 1215), 1) + 100
    rows = [
        ("NFL 2023-25, receptions + reception yds, 1 snapshot", nfl_2mk),
        ("NFL 2025 Thursday opening snapshot (movement test)", nfl_open),
        ("MLB 2025 pitcher_strikeouts, full season", mlb_25),
        ("MLB 2025 pitcher_strikeouts, second half only", mlb_h2),
    ]
    for label, c in rows:
        print(f"  {label:52s} {c:>8,}")
    print()
    print(f"  {'NFL only (first two lines)':52s} "
          f"{nfl_2mk + nfl_open:>8,}")
    print(f"  {'NFL + MLB second half':52s} "
          f"{nfl_2mk + nfl_open + mlb_h2:>8,}")
    print(f"  {'NFL + MLB full 2025':52s} "
          f"{nfl_2mk + nfl_open + mlb_25:>8,}")
    print(f"\n  cap {CAP:,}")


# ---------------------------------------------------------------- driver ---

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true",
                    help="spend credits to find out which books post what")
    ap.add_argument("--max-credits", type=int, default=12)
    args = ap.parse_args()

    spent = {"calls": 0, "credits": 0, "remaining": None}
    counts = game_counts()

    if not args.probe:
        print("DRY RUN - no API calls. Pass --probe to check the markets.\n")
        report_costs(counts)
        return 0

    print("=" * 74)
    print(f"PROBE  (hard budget {args.max_credits} credits)")
    print("=" * 74)
    sports = list_sports(spent)
    active = {s["key"]: s for s in sports if s.get("active")}
    print(f"\n  {len(sports)} sports listed, {len(active)} active")
    for want in ("americanfootball_nfl", "baseball_mlb"):
        print(f"    {want:24s} "
              f"{'ACTIVE' if want in active else 'not in season'}")

    results = {}
    for sport_key, markets in (("americanfootball_nfl", NFL_PLAYER),
                               ("baseball_mlb", MLB_PLAYER + MLB_GAME)):
        if sport_key not in active:
            print(f"\n  {sport_key}: not in season, skipping")
            continue
        print(f"\n{sport_key}")
        evs = list_events(sport_key, spent)
        if not evs:
            continue
        payload = probe_event(sport_key, evs[0]["id"], markets, spent,
                              args.max_credits)
        if not payload:
            continue
        print(f"  probed: {payload.get('away_team')} @ "
              f"{payload.get('home_team')}  {payload.get('commence_time')}")
        found = summarise_books(payload)
        results[sport_key] = found
        for m in markets:
            books = found.get(m)
            if not books:
                print(f"    {m:24s} NOT POSTED by any of our four books")
            else:
                print(f"    {m:24s} {', '.join(sorted(books))}")

    print("\n" + "=" * 74)
    print("THE QUESTION THAT CHANGES THE DESIGN: does Pinnacle post props?")
    print("=" * 74)
    any_pin = False
    for sport_key, found in results.items():
        for m, books in found.items():
            if "pinnacle" in books:
                any_pin = True
                print(f"  YES - {sport_key} {m}")
    if not any_pin:
        print("  NO. Pinnacle posts none of the prop markets probed.")
        print("  => 'fair' for props must be the de-vigged consensus across")
        print("     the US books, and every scored row must record that it")
        print("     used consensus rather than the sharp book. Mixing the two")
        print("     definitions inside one comparison is the failure mode.")

    print(f"\nspent {spent['credits']} credit(s) over {spent['calls']} call(s)"
          f"   remaining {spent['remaining']:,}"
          if spent["remaining"] is not None else "")
    report_costs(counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
