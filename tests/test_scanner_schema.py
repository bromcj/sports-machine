"""The scanner's tables: created by db.init(), additive, and strict where it
matters. Each test gets its own temp database; nothing touches data/."""
import sqlite3

import pytest

import db

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
