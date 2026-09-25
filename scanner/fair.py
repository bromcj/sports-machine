"""fair_value(): the one place the scanner decides what an outcome is worth.

Every strategy calls this. Nothing computes a fair price on its own, so a
difference between two strategies is never a difference between two code
paths. The rule, in order:

  1. pinnacle    Pinnacle's two prices at the latest pull at or before the
                 moment, de-vigged
  2. consensus   the mean de-vigged price across every book with both sides
                 at that same pull
  3. <venue>-mid the market's own best bid and best ask, averaged

Which one answered is returned as `source`, with the moment of the prices
used (`as_of`) and their age. A caller that needs the price to be recent
passes max_age_s, and gets None rather than a stale number.

Three rules that are not optional:

  - Nothing after `at` is ever read. No look-ahead.
  - For a game, nothing captured at or after the start is a market opinion:
    the Odds API keeps serving in-play prices (a real pull returned Giants
    -10000 because they were already winning). Same rule as
    feeds.pregame_books.
  - The de-vig is bets.engine.novig_probs, on the prices as the book quoted
    them - the same arithmetic bets/log.fair_prob does, so the sport model's
    gate and the scanner cannot disagree about a price. audit.py checks that
    they agree exactly.

A yes/no contract on a game (Kalshi's "will Buffalo win") is priced from the
books through its markets.yes_outcome: YES is that canonical outcome, NO is
the other one.
"""
import datetime as dt

from bets.engine import novig_probs
from feeds import parse_utc
from scanner.store import canon_ts
from scanner.venues import allowed, family

SHARP = "sportsbook:pinnacle"

# The two sides of each two-way book market, in the order novig_probs takes
# them (away, home) - so its first return value is the first outcome here.
PAIRS = {"h2h": ("away", "home"), "spread": ("away", "home"),
         "total": ("over", "under")}
OPPOSITE = {"home": "away", "away": "home", "over": "under", "under": "over"}


def canonical_outcome(mkt, outcome: str) -> str | None:
    """The book-side outcome this market's `outcome` corresponds to."""
    if mkt["market_type"] in PAIRS and not mkt["venue"].startswith(("kalshi", "polymarket")):
        return outcome
    yes = mkt["yes_outcome"]
    if not yes or mkt["market_type"] not in PAIRS:
        return None
    if outcome == "yes":
        return yes
    if outcome == "no":
        return OPPOSITE.get(yes)
    return None


def _book_value(con, mkt, want: str, at: str):
    """Pinnacle, else consensus, from the latest pregame pull at or before `at`."""
    first, second = PAIRS[mkt["market_type"]]
    found = con.execute(
        "SELECT market_id, event_start FROM markets WHERE venue LIKE 'sportsbook:%'"
        " AND canonical_event_id=? AND market_type=? AND line IS ?",
        (mkt["canonical_event_id"], mkt["market_type"], mkt["line"])).fetchall()
    if not found:
        return None
    group = [r["market_id"] for r in found]
    # The earliest start anyone gives for this game - the books' and this
    # market's own - and nothing captured at or after it.
    starts = [s for s in [r["event_start"] for r in found] + [mkt["event_start"]] if s]
    cutoff = at
    if starts and min(starts) <= cutoff:
        start = parse_utc(min(starts)) - dt.timedelta(microseconds=1)
        cutoff = canon_ts(start)
    marks = ",".join("?" * len(group))
    pull = con.execute(
        f"SELECT MAX(captured_at) FROM prices WHERE market_id IN ({marks})"
        f" AND quote='ask' AND level=1 AND captured_at <= ?",
        (*group, cutoff)).fetchone()[0]
    if pull is None:
        return None
    rows = con.execute(
        f"SELECT venue, outcome, price_native FROM prices"
        f" WHERE market_id IN ({marks}) AND quote='ask' AND level=1"
        f" AND captured_at=? ORDER BY price_id", (*group, pull)).fetchall()
    books = {}
    for r in rows:
        books.setdefault(r["venue"], {})[r["outcome"]] = int(r["price_native"])
    both = [(v, s) for v, s in books.items() if first in s and second in s]
    if not both:
        return None

    def p_of(sides):
        p_first, p_second = novig_probs(sides[first], sides[second])
        return p_first if want == first else p_second

    sharp = [s for v, s in both if v == SHARP]
    if sharp:
        return p_of(sharp[0]), "pinnacle", pull, [SHARP]
    probs = [p_of(s) for _, s in both]
    return sum(probs) / len(probs), "consensus", pull, [v for v, _ in both]


def _mid(con, mkt, outcome: str, at: str):
    """The market's own best bid and ask for this outcome, averaged."""
    pull = con.execute(
        "SELECT MAX(captured_at) FROM prices WHERE market_id=? AND outcome=?"
        " AND level=1 AND captured_at <= ?",
        (mkt["market_id"], outcome, at)).fetchone()[0]
    if pull is None:
        return None
    q = {r["quote"]: r["price"] for r in con.execute(
        "SELECT quote, price FROM prices WHERE market_id=? AND outcome=?"
        " AND level=1 AND captured_at=?", (mkt["market_id"], outcome, pull))}
    if "bid" not in q or "ask" not in q:
        return None
    return (q["bid"] + q["ask"]) / 2, f"{family(mkt['venue'])}-mid", pull, [mkt["venue"]]


def fair_value(con, market_id: str, outcome: str, at, max_age_s: float | None = None):
    """What `outcome` of `market_id` was worth at `at`, or None.

    Returns {"p", "source", "as_of", "age_s", "books"}.
    """
    mkt = con.execute("SELECT * FROM markets WHERE market_id=?",
                      (market_id,)).fetchone()
    if mkt is None:
        return None
    ok, _ = allowed(mkt["venue"], mkt["sport"])
    if not ok:
        return None
    at = canon_ts(at)
    found = None
    want = canonical_outcome(mkt, outcome)
    if want is not None and mkt["market_type"] in PAIRS:
        found = _book_value(con, mkt, want, at)
        if found and max_age_s is not None and _age(found[2], at) > max_age_s:
            found = None
    if found is None and not mkt["venue"].startswith("sportsbook:"):
        found = _mid(con, mkt, outcome, at)
        if found and max_age_s is not None and _age(found[2], at) > max_age_s:
            found = None
    if found is None:
        return None
    p, source, as_of, books = found
    return {"p": p, "source": source, "as_of": as_of,
            "age_s": _age(as_of, at), "books": books}


def _age(as_of: str, at: str) -> float:
    return (parse_utc(at) - parse_utc(as_of)).total_seconds()
