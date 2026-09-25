"""The markets and prices tables: keys, checked inserts, and "what was on
offer at that moment".

ONE TIMESTAMP FORMAT. Every time written to a scanner table goes through
canon_ts(): aware UTC, always with microseconds, '+00:00'. Queries compare
these as strings, which is only correct when every value has the same shape:
'...T23:20:00+00:00' sorts BEFORE '...T23:20:00Z' ('+' < 'Z'), and
'...:33+00:00' sorts before '...:33.000000+00:00' although they are the same
instant. This codebase has lost a closing line to the first of those already
(bets/log.closing_snapshot). Here it cannot happen, because nothing else is
ever stored.
"""
import datetime as dt

from bets.engine import american_to_decimal
from feeds import parse_utc
from ingest import quality
from scanner import fees, venues

QUOTES = ("ask", "bid")


def canon_ts(value) -> str:
    """Any timestamp -> 'YYYY-MM-DDTHH:MM:SS.ffffff+00:00'. Raises if unusable."""
    when = value if isinstance(value, dt.datetime) else parse_utc(value)
    if when is None:
        raise ValueError(f"not a timestamp: {value!r}")
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return when.astimezone(dt.timezone.utc).isoformat(timespec="microseconds")


def line_key(line) -> str:
    return "" if line is None else f"{float(line):g}"


def market_key(venue: str, venue_market_id: str, market_type: str, line=None) -> str:
    """Our id for one venue's contract. The same inputs always give the same key,
    so a re-poll lands on the same market row."""
    return f"{venue}|{venue_market_id}|{market_type}|{line_key(line)}"


# ---------------------------------------------------------------- checks ---

# A row these pass must be storable: nothing in it may make the insert raise
# (canon_ts on a bad time did, mid-batch, with nothing counted).

def _bad_time(row: dict, key: str) -> str | None:
    if row.get(key) in (None, ""):
        return None
    try:
        canon_ts(row[key])
    except (TypeError, ValueError):
        return f"{key} {row[key]!r} is not a timestamp"
    return None


def check_market(m: dict) -> str | None:
    """Reason to reject a markets row, or None to keep it."""
    for k in ("market_id", "venue", "venue_market_id", "canonical_event_id",
              "market_type", "first_seen"):
        if not m.get(k):
            return f"market has no {k}"
    try:
        venues.family(m["venue"])
    except ValueError as e:
        return str(e)
    if m["market_type"] not in ("h2h", "spread", "total", "prop", "futures", "binary"):
        return f"unknown market_type {m['market_type']!r}"
    for k in ("first_seen", "event_start", "resolves_at"):
        bad = _bad_time(m, k)
        if bad:
            return bad
    return None


def check_price(p: dict) -> str | None:
    """Reason to reject a prices row, or None to keep it."""
    for k in ("market_id", "venue", "outcome", "price_native", "fee_model",
              "captured_at"):
        if p.get(k) in (None, ""):
            return f"price has no {k}"
    try:
        fam = venues.family(p["venue"])
    except ValueError as e:
        return str(e)
    if p.get("quote") not in QUOTES:
        return f"quote must be ask or bid, got {p.get('quote')!r}"
    try:
        whole = float(p.get("level")) == int(p.get("level"))
    except (TypeError, ValueError, OverflowError):
        whole = False
    if not whole or int(p["level"]) < 1:
        return f"level must be a whole number, 1 or more, got {p.get('level')!r}"
    for k in ("captured_at", "source_last_update"):
        bad = _bad_time(p, k)
        if bad:
            return bad
    if fam == "sportsbook":
        bad = quality.moneyline(p["price_native"])     # the ingest rule
        if bad:
            return bad
    try:
        price = float(p["price"])
    except (TypeError, ValueError):
        return f"price {p.get('price')!r} is not a number"
    if not 0.0 < price < 1.0:
        return f"price {price} is not a probability strictly inside (0, 1)"
    size = p.get("size_available")
    if fam == "sportsbook":
        if size is not None:
            return "a book row has no size: size_available must be NULL"
        # fair_value reads price_native, a paper fill reads price: they must
        # be the same number, by the formula both book adapters use.
        want = 1 / american_to_decimal(int(p["price_native"]))
        if abs(price - want) > 1e-12:
            return f"book price {price} is not its moneyline {p['price_native']} ({want})"
    elif size is not None:
        try:
            ok = float(size) >= 0                      # False for NaN too
        except (TypeError, ValueError):
            ok = False
        if not ok:
            return f"size {size!r} is negative or not a number"
    # The key must be well formed. Whether its fee can be computed yet (a
    # Kalshi `flat` series cannot) is decided where an EV is needed, not
    # here: a price is worth keeping even before its fee table is read.
    if not fees.well_formed(p["fee_model"]):
        return f"fee model {p['fee_model']!r} is not a known key format"
    # And it must be this venue's: paper fills charge the fee of the price
    # row, so a Kalshi price tagged 'book' would trade as if Kalshi were free.
    if _fee_family(p["fee_model"]) != fam:
        return f"fee model {p['fee_model']!r} is not a {fam} fee"
    return None


def _fee_family(model: str) -> str:
    """'kalshi:quadratic:1' -> 'kalshi'; 'book' -> 'sportsbook'."""
    head = str(model).split(":", 1)[0]
    return "sportsbook" if head == "book" else head


# ---------------------------------------------------------------- writes ---

# Whether a sighting's start replaces the stored one. An earlier start always
# does. A later one (a rain delay, a flexed kickoff) only if the sighting was
# made BEFORE that new start - `first_seen` on an incoming row is when it was
# seen. A later start first reported after it had passed is a feed correcting
# itself mid-game (Astros @ Rockies 2024-04-27: first pitch 22:05, re-reported
# as 22:26:59 at 23:55); taking it would turn in-play prices into pregame ones.
_MOVES = ("markets.event_start IS NULL OR excluded.event_start <= markets.event_start"
          " OR excluded.first_seen < excluded.event_start")


def upsert_markets(con, rows, rejects=None) -> int:
    """Insert new markets. A later sighting may fill a start or resolution time
    that was missing, or move one (_MOVES); a NULL never erases one."""
    n = 0
    for m in rows:
        bad = check_market(m)
        if rejects is not None and not rejects.check(bad):
            continue
        if bad:
            raise ValueError(bad)
        con.execute(
            "INSERT INTO markets (market_id, venue, venue_market_id, sport,"
            " canonical_event_id, market_type, line, yes_outcome, event_start,"
            " resolves_at, resolution_source, first_seen)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(market_id) DO UPDATE SET"
            "   event_start=CASE WHEN excluded.event_start IS NULL THEN markets.event_start"
            f"    WHEN {_MOVES} THEN excluded.event_start ELSE markets.event_start END,"
            "   resolves_at=CASE WHEN excluded.resolves_at IS NULL THEN markets.resolves_at"
            f"    WHEN excluded.event_start IS NULL OR {_MOVES} THEN excluded.resolves_at"
            "    ELSE markets.resolves_at END",
            (m["market_id"], m["venue"], m["venue_market_id"], m.get("sport"),
             m["canonical_event_id"], m["market_type"], m.get("line"),
             m.get("yes_outcome"),
             canon_ts(m["event_start"]) if m.get("event_start") else None,
             canon_ts(m["resolves_at"]) if m.get("resolves_at") else None,
             m.get("resolution_source"), canon_ts(m["first_seen"])))
        n += 1
    return n


def insert_prices(con, rows, rejects=None) -> int:
    """Append prices. The same observation twice is ignored, never an error."""
    n = 0
    for p in rows:
        bad = check_price(p)
        if rejects is not None and not rejects.check(bad):
            continue
        if bad:
            raise ValueError(bad)
        cur = con.execute(
            "INSERT OR IGNORE INTO prices (market_id, venue, outcome, quote, level,"
            " price, price_native, size_available, fee_model, captured_at,"
            " source_last_update, raw_ref) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (p["market_id"], p["venue"], p["outcome"], p["quote"],
             int(p.get("level") or 1), float(p["price"]), str(p["price_native"]),
             None if p.get("size_available") is None else float(p["size_available"]),
             p["fee_model"], canon_ts(p["captured_at"]),
             canon_ts(p["source_last_update"]) if p.get("source_last_update") else None,
             p.get("raw_ref")))
        n += cur.rowcount
    return n


# ---------------------------------------------------------------- reads ---

def market(con, market_id: str):
    return con.execute("SELECT * FROM markets WHERE market_id=?",
                       (market_id,)).fetchone()


def next_quotes_after(con, market_id: str, outcome: str, quote: str, after):
    """Every level of the FIRST observation strictly after `after`. The fill
    simulator's only view of the market, so it can never see the price that
    was showing when the order went in."""
    after = canon_ts(after)
    first = con.execute(
        "SELECT MIN(captured_at) FROM prices WHERE market_id=? AND outcome=?"
        " AND quote=? AND captured_at > ?",
        (market_id, outcome, quote, after)).fetchone()[0]
    if first is None:
        return []
    return con.execute(
        "SELECT * FROM prices WHERE market_id=? AND outcome=? AND quote=?"
        " AND captured_at=? ORDER BY level",
        (market_id, outcome, quote, first)).fetchall()
