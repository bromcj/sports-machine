"""shop / info / ev, through the real bets.paper._decompose_bet on a temp DB.

This file used to test its own copy of the three formulas, so replacing
_decompose_bet with `return True` passed it.
"""
import pytest

import db
from bets import paper

BET_TS, CLOSE_TS = "2026-09-23T00:53:52+00:00", "2026-09-23T23:37:24+00:00"


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "machine.db")
    db.init()
    c = db.connect()
    yield c
    c.close()


def _setup(con, at_bet, at_close, line_taken, side="away"):
    """Two pulls of Pinnacle prices; a bet taken at the first."""
    ids = {}
    for ts, (a, h) in ((BET_TS, at_bet), (CLOSE_TS, at_close)):
        cur = con.execute(
            "INSERT INTO odds_snapshots (game_id, sport, ts, book, away_ml,"
            " home_ml, snapshot_type, commence_time) VALUES (?,?,?,?,?,?,?,?)",
            ("mlb-oddsrow", "mlb", ts, "pinnacle", a, h, "open",
             "2026-09-23T23:41:00Z"))
        ids[ts] = cur.lastrowid
    cur = con.execute(
        "INSERT INTO bets (mode, ts, game_id, sport, side, book, line_taken,"
        " stake, model_prob, novig_market_prob, edge, kelly_fraction,"
        " odds_snapshot_id) VALUES ('paper',?,?,?,?,?,?,10,0.5,0.5,0,0.25,?)",
        (BET_TS, "mlb-824060", "mlb", side, "draftkings", line_taken, ids[BET_TS]))
    con.commit()
    bet = con.execute("SELECT * FROM bets WHERE bet_id=?", (cur.lastrowid,)).fetchone()
    close = dict(con.execute("SELECT * FROM odds_snapshots WHERE id=?",
                             (ids[CLOSE_TS],)).fetchone())
    assert paper._decompose_bet(con, bet, close)
    return con.execute("SELECT shop_pct, info_pct, ev_fair_close FROM bets"
                       " WHERE bet_id=?", (bet["bet_id"],)).fetchone()


def test_the_identity_is_exact(con):
    r = _setup(con, (+100, -110), (-110, +100), line_taken=+105)
    shop, info, ev = (x / 100 for x in r)
    assert abs((1 + shop) * (1 + info) - (1 + ev)) < 1e-6


def test_pure_line_shopping_scores_nothing_on_info(con):
    # A better price than fair, but the fair line never moved. This is the case
    # that could otherwise pass gate 2 with no forecasting skill at all.
    r = _setup(con, (+100, -120), (+100, -120), line_taken=+130)
    assert r["shop_pct"] > 5 and abs(r["info_pct"]) < 1e-9


def test_a_line_moving_the_models_way_scores_on_info(con):
    r = _setup(con, (+100, -120), (-130, +110), line_taken=+100)
    assert r["info_pct"] > 10


def test_a_line_moving_against_the_model_scores_negative(con):
    r = _setup(con, (-130, +110), (+100, -120), line_taken=-130)
    assert r["info_pct"] < -10
