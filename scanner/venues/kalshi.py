"""Kalshi order books into prices rows. Translation only: finding markets and
matching them to games is Phase C (docs/venues.md).

The book, as documented 2026-09-25 (GET /markets/{ticker}/orderbook):

    {"orderbook_fp": {"yes_dollars": [["0.4500", "120.00"], ...],
                      "no_dollars":  [["0.5300", "40.00"], ...]}}

BIDS ONLY, on both sides. There are no asks in the payload because a YES bid
at X is a NO ask at 1 - X. So the price to BUY yes is 1 - (best NO bid), in
the size of that NO bid, and the other way round. Levels are sorted here, not
trusted from the payload.

Every Kalshi path asks scanner.venues.allowed() first; with the legal kill
switch off, a sports market produces no rows at all.
"""
from decimal import Decimal

from scanner import fees
from scanner.store import canon_ts, market_key
from scanner.venues import allowed

VENUE = "kalshi"
LEVELS = 3


def market_id(ticker: str, market_type: str = "binary", line=None) -> str:
    return market_key(VENUE, ticker, market_type, line)


def market_row(ticker: str, *, canonical_event_id: str, first_seen,
               sport: str | None = None, market_type: str = "binary",
               yes_outcome: str | None = None, line=None, event_start=None,
               resolves_at=None, resolution_source: str | None = None) -> dict:
    """One markets row. The mapping to a canonical event is the caller's job,
    done through feeds (start time plus names), never a date string."""
    ok, why = allowed(VENUE, sport)
    if not ok:
        raise PermissionError(why)
    return {"market_id": market_id(ticker, market_type, line), "venue": VENUE,
            "venue_market_id": ticker, "sport": sport,
            "canonical_event_id": canonical_event_id,
            "market_type": market_type, "line": line, "yes_outcome": yes_outcome,
            "event_start": event_start, "resolves_at": resolves_at,
            "resolution_source": resolution_source, "first_seen": first_seen}


def fee_model(series: dict) -> str:
    """The fee key from a series object's fee_type and fee_multiplier."""
    return fees.kalshi_key(series["fee_type"], series.get("fee_multiplier", 1))


def _levels(side: list) -> list[tuple[Decimal, Decimal, str]]:
    out = []
    for level in side or []:
        price, qty = Decimal(str(level[0])), Decimal(str(level[1]))
        if Decimal(0) < price < Decimal(1) and qty > 0:
            out.append((price, qty, str(level[0])))
    return out


def price_rows(mkt_id: str, payload: dict, captured_at, fee_model_key: str,
               sport: str | None = None, raw_ref: str | None = None) -> list[dict]:
    """Bids and asks, both outcomes, top three levels each."""
    ok, why = allowed(VENUE, sport)
    if not ok:
        return []
    book = (payload or {}).get("orderbook_fp")
    if not isinstance(book, dict):
        raise ValueError("not a Kalshi order book: no 'orderbook_fp' object")
    bids = {"yes": _levels(book.get("yes_dollars")),
            "no": _levels(book.get("no_dollars"))}
    captured = canon_ts(captured_at)
    rows = []

    def add(outcome, quote, levels, derived_from=None):
        for i, (price, qty, native) in enumerate(levels[:LEVELS], start=1):
            rows.append({
                "market_id": mkt_id, "venue": VENUE, "outcome": outcome,
                "quote": quote, "level": i, "price": float(price),
                "price_native": native if derived_from is None
                                else f"1-{derived_from}_bid:{native}",
                "size_available": float(qty), "fee_model": fee_model_key,
                "captured_at": captured, "source_last_update": None,
                "raw_ref": raw_ref})

    for outcome, other in (("yes", "no"), ("no", "yes")):
        best_bids = sorted(bids[outcome], key=lambda x: -x[0])
        add(outcome, "bid", best_bids)
        # What it costs to BUY this outcome: the other side's bids, flipped.
        asks = sorted(((1 - p, q, n) for p, q, n in bids[other]), key=lambda x: x[0])
        add(outcome, "ask", asks, derived_from=other)
    return rows
