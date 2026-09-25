"""The scanner's tables: created by db.init(), additive, and strict where it
matters. Each test gets its own temp database; nothing touches data/."""
import sqlite3

import pytest

import db
from bets.engine import american_to_decimal
from ingest.quality import Rejects
from scanner import store

SCANNER_TABLES = {"markets", "prices", "credit_ledger", "paper_orders",
                  "paper_fills", "paper_positions", "gate_looks"}


@pytest.fixture
def con(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init()
    capsys.readouterr()
    c = db.connect()
    yield c
    c.close()


def _order(con, **over):
    row = dict(strategy="t", mode="paper", venue="kalshi", market_id="m",
               outcome="yes", role="taker", size=1, limit_price=0.5,
               placed_at="2026-09-25T00:00:00+00:00", exposure=0.52)
    row.update(over)
    cols = ",".join(row)
    con.execute(f"INSERT INTO paper_orders ({cols}) VALUES"
                f" ({','.join('?' * len(row))})", tuple(row.values()))


def test_init_creates_every_scanner_table(con):
    have = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert SCANNER_TABLES <= have


def test_the_database_refuses_an_order_that_is_not_paper(con):
    _order(con)                                   # paper: fine
    _order(con, mode="placebo")                   # placebo: fine
    for bad in ("real", "live", "", "PAPER"):
        with pytest.raises(sqlite3.IntegrityError):
            _order(con, mode=bad)


def test_an_order_must_be_taker_or_maker(con):
    with pytest.raises(sqlite3.IntegrityError):
        _order(con, role="market")


def test_the_same_price_observation_cannot_be_stored_twice(con):
    row = ("m", "kalshi", "yes", "ask", 1, 0.45, "0.4500", 10.0,
           "kalshi:quadratic:1", "2026-09-25T00:00:00+00:00")
    sql = ("INSERT INTO prices (market_id, venue, outcome, quote, level, price,"
           " price_native, size_available, fee_model, captured_at)"
           " VALUES (?,?,?,?,?,?,?,?,?,?)")
    con.execute(sql, row)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(sql, row)
    # A different level, side of the book or moment is a different observation.
    con.execute(sql, row[:4] + (2,) + row[5:])
    con.execute(sql, row[:3] + ("bid",) + row[4:])
    con.execute(sql, row[:9] + ("2026-09-25T00:01:00+00:00",))


# ------------------------------------------------ store's checked inserts ---
# The checks are all that stands between an adapter and these tables. A row
# they pass must be storable; a row the schema forbids comes back as a
# reason - counted, never stored, never an exception mid-batch.

T0 = "2026-09-27T15:00:00+00:00"
KAL = {"market_id": "k1", "venue": "kalshi", "outcome": "yes", "quote": "ask",
       "level": 1, "price": 0.45, "price_native": "0.4500", "size_available": 10.0,
       "fee_model": "kalshi:quadratic:1", "captured_at": T0}
BOOK = {"market_id": "b1", "venue": "sportsbook:draftkings", "outcome": "home",
        "quote": "ask", "level": 1, "price": 1 / american_to_decimal(-110),
        "price_native": "-110", "size_available": None, "fee_model": "book",
        "captured_at": T0}
MKT = {"market_id": "k1", "venue": "kalshi", "venue_market_id": "K1",
       "canonical_event_id": "weather:KNYC:2026-10-01", "market_type": "binary",
       "first_seen": T0}


def test_good_rows_pass_the_checks(con):
    pm = dict(KAL, market_id="p1", venue="polymarket", fee_model="polymarket:0.05",
              source_last_update="2026-09-27T14:59:00Z")
    game = dict(MKT, market_id="k2", event_start="2026-10-01T23:05:00Z",
                resolves_at="2026-10-02T02:20:00Z")
    for row in (KAL, BOOK, pm):
        assert store.check_price(row) is None
    assert store.check_market(MKT) is None and store.check_market(game) is None
    assert store.upsert_markets(con, [MKT, game]) == 2
    assert store.insert_prices(con, [KAL, BOOK, pm]) == 3


@pytest.mark.parametrize("row", [
    dict(KAL, venue="predictit"), dict(BOOK, venue="sportsbook:"),
    dict(KAL, venue="Kalshi"),
    dict(BOOK, size_available=100.0),                  # a book row has no size
    dict(BOOK, price=0.9),                             # fills at 0.9, fair reads -110
    dict(KAL, level=1.7), dict(KAL, level="abc"), dict(KAL, level=0),
    dict(KAL, size_available=-1.0),
    dict(KAL, size_available="abc"), dict(KAL, size_available=float("nan")),
    dict(KAL, captured_at="yesterday"), dict(KAL, source_last_update="soon"),
])
def test_a_bad_price_row_is_a_reason_never_a_crash(con, row):
    assert store.check_price(row) is not None
    r = Rejects()
    assert store.insert_prices(con, [row], rejects=r) == 0
    assert len(r) == 1


def test_a_price_under_another_venues_market_is_a_reason(con):
    # Each row passes check_price on its own. Filed under another venue's
    # market, a Kalshi row made fair_value crash ('0.9000' is no moneyline)
    # and could fill a book order at the exchange's price; a Pinnacle row
    # under DraftKings' market would be counted as Pinnacle.
    dk = dict(MKT, market_id="b1", venue="sportsbook:draftkings", venue_market_id="e1",
              canonical_event_id="nfl-e1", market_type="h2h")
    store.upsert_markets(con, [MKT, dk])
    bad = [dict(KAL, market_id="b1"), dict(BOOK, market_id="k1"),
           dict(BOOK, venue="sportsbook:pinnacle")]
    for row in bad:
        assert store.check_price(row) is None
    r = Rejects()
    assert store.insert_prices(con, bad, rejects=r) == 0 and len(r) == len(bad)
    with pytest.raises(ValueError, match="market"):
        store.insert_prices(con, bad[:1])
    assert store.insert_prices(con, [KAL, BOOK]) == 2       # each under its own market


@pytest.mark.parametrize("row", [
    dict(MKT, venue="predictit"), dict(MKT, venue="sportsbook:"),
    dict(MKT, venue="Kalshi"),
    {k: v for k, v in MKT.items() if k != "first_seen"}, dict(MKT, first_seen=None),
    dict(MKT, first_seen="yesterday"),
    dict(MKT, event_start="tonight"), dict(MKT, resolves_at="soon"),
])
def test_a_bad_market_row_is_a_reason_never_a_crash(con, row):
    assert store.check_market(row) is not None
    r = Rejects()
    assert store.upsert_markets(con, [row], rejects=r) == 0
    assert len(r) == 1
