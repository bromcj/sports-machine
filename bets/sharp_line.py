"""Phase 3: flag any book beating the sharp line. A control, and a question.

    python run_daily.py shop            # scan what has been collected, report
    python -m bets.sharp_line --historical   # the same scan on the 4-book archive

IT HAS TWO PURPOSES AND BOTH MATTER.

**It is +EV by construction, so it is a test of our own code.** Pinnacle's
de-vigged price is the fair probability; a book paying more than that on the
same outcome is a positive-expectation bet by arithmetic, before any modelling.
So if this engine reports a NEGATIVE expectation over a decent sample, the
fair-line construction or the grading path is broken - and every other number
in this project that leans on `fair_prob` is suspect. That is the sanity check
nothing else provides.

**And it answers the cheapest open question left.** E4 measured "almost nothing
to shop": across DraftKings, FanDuel and BetMGM against Pinnacle, betting every
price returned -3.8% to -4.4%, and inside 90 minutes of first pitch a price
worth more than 1.5% EV appeared about once in 104 games. But that was measured
on FOUR books. A real shop watches twelve. The Phase 0 pulls name fifteen, so
this is the same measurement with three times the coverage - and it is free,
because the prices are being collected anyway.

WHAT IT IS NOT. It never stakes money and it never feeds a gate. Flags are
logged as `mode='shop'` and are kept out of every model report, for the reason
the Phase 5 EV decomposition exists: shopping value and forecasting value are
different things, and a model must never be credited with the first.

HOW EV IS COMPUTED. Against the posted American price, not a de-vigged gap:

    EV = p_fair(Pinnacle, de-vigged) x decimal odds at the soft book - 1

which is positive only when the soft book pays more than the sharp book thinks
the outcome is worth, AFTER the soft book's own margin.
"""
import argparse
import gzip
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import paths
from db import connect
from bets.engine import american_to_decimal, novig_probs

SHARP = "pinnacle"
MIN_EV = 0.015          # 1.5%, the threshold the brief starts at
RAW = paths.DATA_DIR / "props_live" / "raw"


def _devig(a, h):
    if a is None or h is None:
        return None, None
    return novig_probs(a, h)


# ------------------------------------------------------- the live scanner ---

def _iter_live_games():
    """Every collected game-market payload: (sport, stamp, event)."""
    for sport_dir in sorted(RAW.glob("*")):
        for stamp_dir in sorted(sport_dir.glob("*")):
            f = stamp_dir / "game" / "slate.json.gz"
            if not f.exists():
                continue
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                try:
                    payload = json.load(fh)
                except json.JSONDecodeError:
                    continue
            for ev in payload:
                yield sport_dir.name, stamp_dir.name, ev


def scan_live(min_ev: float = MIN_EV) -> dict:
    """Flags from the Phase 0 collection - the wide book list."""
    from feeds import parse_utc
    import datetime as _dt
    flags, games, books_seen = [], set(), set()
    leads = []
    for sport, stamp, ev in _iter_live_games():
        gid = ev.get("id")
        games.add(gid)
        # How far from kickoff this price was taken. E4's number is a
        # NEAR-CLOSE number; a flag count at long lead times is a different
        # quantity and must not be read against it. The slate request returns
        # the whole board, so most of these are days out.
        start = parse_utc(ev.get("commence_time"))
        try:
            taken = _dt.datetime.strptime(stamp[:11], "%Y%m%dT%H").replace(
                tzinfo=_dt.timezone.utc)
        except ValueError:
            taken = None
        if start and taken:
            leads.append((start - taken).total_seconds() / 3600.0)
        prices = {}
        for bk in ev.get("bookmakers", []):
            books_seen.add(bk["key"])
            for mkt in bk.get("markets", []):
                if mkt["key"] != "h2h":
                    continue
                o = {x["name"]: x["price"] for x in mkt.get("outcomes", [])}
                away, home = ev.get("away_team"), ev.get("home_team")
                if away in o and home in o:
                    prices[bk["key"]] = (o[away], o[home])
        if SHARP not in prices:
            continue
        pa, ph = _devig(*prices[SHARP])
        if pa is None:
            continue
        for book, (a, h) in prices.items():
            if book == SHARP:
                continue
            for side, fair, ml in (("away", pa, a), ("home", ph, h)):
                ev_pct = fair * american_to_decimal(ml) - 1
                if ev_pct > min_ev:
                    flags.append({
                        "sport": sport, "stamp": stamp, "game_id": gid,
                        "matchup": f"{ev.get('away_team')} @ "
                                   f"{ev.get('home_team')}",
                        "book": book, "side": side, "price": ml,
                        "p_fair": fair, "ev": ev_pct,
                    })
    leads.sort()
    return {"flags": flags, "games": len(games), "books": sorted(books_seen),
            "median_lead_h": leads[len(leads) // 2] if leads else None}


# -------------------------------------------------- the historical scanner --

def scan_historical(min_ev: float = MIN_EV, max_lead_min: float = 90.0) -> dict:
    """The same scan on the four-book archive. Reproduces E4 as a code check.

    E4's DEFINITION HAS TO BE MATCHED OR THIS IS NOT A CROSS-CHECK. Two things
    caught me out on the first run, and the disagreement was mine, not E4's:

      1. E4 restricted to prices within 90 MINUTES of first pitch - what a live
         shop would actually be looking at. Scanning every snapshot at every
         lead time includes prices days out, where books disagree far more and
         nobody is standing at a terminal.
      2. E4 counted GAMES OFFERING a worthwhile price ("one in 104 games"),
         not raw flags. One game can produce a dozen flags - several books,
         two sides, several timestamps - so the two numbers differ by an order
         of magnitude while describing the same market.

    Both are reported now, with the lead-time filter, so a real disagreement
    would be visible instead of drowned in a definition mismatch.
    """
    con = connect()
    rows = con.execute(
        "SELECT s.game_id, s.ts, s.book, s.away_ml, s.home_ml,"
        " s.commence_time FROM odds_snapshots s"
        " WHERE s.away_ml IS NOT NULL AND s.home_ml IS NOT NULL"
        " AND s.commence_time IS NOT NULL").fetchall()
    con.close()
    from feeds import parse_utc
    by = defaultdict(dict)
    leads = {}
    for r in rows:
        start, taken = parse_utc(r["commence_time"]), parse_utc(r["ts"])
        if start is None or taken is None:
            continue
        lead = (start - taken).total_seconds() / 60.0
        if not 0 < lead <= max_lead_min:
            continue
        key = (r["game_id"], r["ts"])
        by[key][r["book"]] = (r["away_ml"], r["home_ml"])
        leads[key] = lead

    flags, n_prices, games = 0, 0, set()
    flagged_games = set()
    ev_sum, ev_n = 0.0, 0
    per_book = defaultdict(lambda: [0.0, 0])
    for (gid, ts), prices in by.items():
        if SHARP not in prices:
            continue
        games.add(gid)
        pa, ph = _devig(*prices[SHARP])
        if pa is None:
            continue
        for book, (a, h) in prices.items():
            if book == SHARP:
                continue
            for fair, ml in ((pa, a), (ph, h)):
                e = fair * american_to_decimal(ml) - 1
                n_prices += 1
                ev_sum += e
                ev_n += 1
                per_book[book][0] += e
                per_book[book][1] += 1
                if e > min_ev:
                    flags += 1
                    flagged_games.add(gid)
    return {"flags": flags, "prices": n_prices, "games": len(games),
            "flagged_games": len(flagged_games),
            "mean_ev": (ev_sum / ev_n) if ev_n else None,
            "by_book": {b: (s / n) for b, (s, n) in per_book.items() if n}}


# ------------------------------------------------------------- reporting ---

def report(min_ev: float = MIN_EV, historical: bool = False) -> int:
    print("=" * 74)
    print("SHARP vs SOFT - a control, and the widest measurement we have")
    print("=" * 74)
    print(f"\nflagging any book paying more than {100 * min_ev:.1f}% EV "
          f"against Pinnacle's de-vigged price\n")

    live = scan_live(min_ev)
    print(f"LIVE collection (Phase 0): {live['games']} game(s), "
          f"{len(live['books'])} books")
    print(f"  books: {', '.join(live['books']) or 'none yet'}")
    if live["games"]:
        per100 = 100 * len(live["flags"]) / live["games"]
        lead = live.get("median_lead_h")
        print(f"  flags: {len(live['flags'])}  "
              f"({per100:.1f} per 100 games)")
        if lead is not None:
            print(f"  median lead: {lead:.0f} h before kickoff - so this is "
                  f"NOT comparable\n  to E4's near-close number below. Books "
                  f"disagree far more days out.")
        for f in sorted(live["flags"], key=lambda x: -x["ev"])[:10]:
            print(f"    {f['matchup'][:38]:38s} {f['book']:12s} "
                  f"{f['side']:4s} {f['price']:>5} "
                  f"EV {100 * f['ev']:+5.2f}%")
    else:
        print("  nothing collected yet - the schedule pulls at 11:45, 15:45, "
              "18:00, 20:00 and 21:00 ET")

    if historical:
        print("\nHISTORICAL archive (4 books), within 90 min of first pitch")
        print("- E4's own definition, so this is a cross-check and not a")
        print("  different question wearing the same name")
        h = scan_historical(min_ev)
        print(f"\n  {h['games']:,} games, {h['prices']:,} soft prices with a "
              f"Pinnacle price at the same instant")
        print(f"  games offering >{100 * min_ev:.1f}% EV: "
              f"{h['flagged_games']:,} of {h['games']:,}  "
              f"(1 in {h['games'] / max(h['flagged_games'], 1):.0f})")
        print(f"  raw flags: {h['flags']:,}  (a game can flag several times - "
              f"several books, two sides)")
        print(f"\n  mean EV per soft price, by book:")
        for b, e in sorted(h["by_book"].items(), key=lambda kv: kv[1]):
            print(f"    {b:12s} {100 * e:+.2f}%")
        print(f"    {'ALL':12s} {100 * (h['mean_ev'] or 0):+.2f}%")
        print("\n  E4 published -4.24% (DK), -3.81% (FD), -4.40% (MGM) and")
        print("  about one worthwhile price per 104 games. Numbers close to")
        print("  those mean the fair-line and grading paths agree with the")
        print("  published result; a materially different one means something")
        print("  is wrong, which is what this control is for.")

    print("\n" + "-" * 74)
    print("This never stakes money and never feeds a gate. Flags are kept")
    print("separate from model results, because shopping value and")
    print("forecasting value are different things and the model must never")
    print("be credited with the first.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-ev", type=float, default=MIN_EV)
    ap.add_argument("--historical", action="store_true")
    a = ap.parse_args()
    raise SystemExit(report(a.min_ev, a.historical))
