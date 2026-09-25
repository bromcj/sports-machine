"""Polymarket order books into prices rows. A price source only: nothing is
ever executed there in this brief (scanner.venues.VENUES).

The book, as documented 2026-09-25 (GET https://clob.polymarket.com/book):

    {"market": "0x...", "asset_id": "<token id>", "timestamp": "...",
     "bids": [{"price": "0.48", "size": "1000"}, ...],
     "asks": [{"price": "0.52", "size": "800"}, ...], ...}

One book per outcome token, so the caller says which outcome it is. Levels are
sorted here - best bid highest, best ask lowest - not trusted from the payload.
Whether the US-regulated Polymarket is open to a NJ resident is a Phase C
question (docs/venues.md).
"""
import datetime as dt
from decimal import Decimal

from scanner.store import canon_ts, market_key

VENUE = "polymarket"
LEVELS = 3


def market_id(condition_id: str, market_type: str = "binary", line=None) -> str:
    return market_key(VENUE, condition_id, market_type, line)


def _when(stamp):
    """The book's own timestamp: ISO text, or epoch milliseconds as digits."""
    if stamp is None:
        return None
    s = str(stamp).strip()
    if s.isdigit():
        return canon_ts(dt.datetime.fromtimestamp(int(s) / 1000, dt.timezone.utc))
    try:
        return canon_ts(s)
    except ValueError:
        return None


def price_rows(mkt_id: str, outcome: str, payload: dict, captured_at,
               fee_model_key: str, raw_ref: str | None = None) -> list[dict]:
    if not isinstance(payload, dict) or "bids" not in payload or "asks" not in payload:
        raise ValueError("not a Polymarket book: needs 'bids' and 'asks'")
    captured = canon_ts(captured_at)
    updated = _when(payload.get("timestamp"))
    rows = []
    for quote, levels, best_first in (("bid", payload["bids"], True),
                                      ("ask", payload["asks"], False)):
        clean = []
        for lv in levels or []:
            price, size = Decimal(str(lv["price"])), Decimal(str(lv["size"]))
            if Decimal(0) < price < Decimal(1) and size > 0:
                clean.append((price, size, str(lv["price"])))
        clean.sort(key=lambda x: x[0], reverse=best_first)
        for i, (price, size, native) in enumerate(clean[:LEVELS], start=1):
            rows.append({
                "market_id": mkt_id, "venue": VENUE, "outcome": outcome,
                "quote": quote, "level": i, "price": float(price),
                "price_native": native, "size_available": float(size),
                "fee_model": fee_model_key, "captured_at": captured,
                "source_last_update": updated, "raw_ref": raw_ref})
    return rows
