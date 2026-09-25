"""Paper execution: fills against the NEXT observed price only, levels and
sizes respected, the exposure cap enforced before an order exists, and the
capital each position ties up."""
import datetime as dt

import pytest

import config
import db
from scanner import capital, paper, store
from scanner.paper import Refused
from scanner.venues import kalshi, sportsbook

UTC = dt.timezone.utc
T = dt.datetime(2026, 11, 3, 18, 0, tzinfo=UTC)
START = "2026-11-04T00:10:00Z"                         # 7:10pm ET
K = "kalshi:quadratic:1"


def at(minutes):
    return (T + dt.timedelta(minutes=minutes)).isoformat()


@pytest.fixture
def con(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(config, "STRATEGY_DAILY_EXPOSURE", {"default": 1000.0})
    db.init()
    capsys.readouterr()
    c = db.connect()
    yield c
    c.close()


def _kmarket(con, sport=None, fee=K):
    row = kalshi.market_row("KXTEST", canonical_event_id="test:1", first_seen=at(0),
                            sport=sport, resolves_at="2026-11-04T04:00:00Z")
    store.upsert_markets(con, [row])
    return row["market_id"], fee


def _book(con, mid, minute, no_bids, yes_bids=(("0.3000", "10"),), fee=K, sport=None):
    """A Kalshi book at T+minute. YES asks are 1 - the NO bids given."""
    store.insert_prices(con, kalshi.price_rows(
        mid, {"orderbook_fp": {"yes_dollars": [list(b) for b in yes_bids],
                               "no_dollars": [list(b) for b in no_bids]}},
        at(minute), fee, sport=sport))


def _order(con, mid, **kw):
    args = dict(strategy="t", mode="paper", market_id=mid, outcome="yes",
                role="taker", size=100, limit_price=0.50, now=at(0))
    args.update(kw)
    return paper.submit(con, **args)


def _status(con, oid):
    return con.execute("SELECT status FROM paper_orders WHERE order_id=?",
                       (oid,)).fetchone()[0]


# ------------------------------------------------------- no look-ahead ---

def test_a_taker_never_fills_at_the_price_showing_when_it_was_placed(con):
    mid, _ = _kmarket(con)
    _book(con, mid, -5, [("0.6000", "500")])     # yes ask 0.40: a bargain, but before
    _book(con, mid, 0, [("0.6000", "500")])      # ...and AT the moment of the order
    oid = _order(con, mid)
    paper.simulate(con, at(0.5))
    assert _status(con, oid) == "open"           # nothing after it yet: waits
    _book(con, mid, 1, [("0.4800", "500")])      # the next one: yes ask 0.52 > limit
    paper.simulate(con, at(1))
    assert _status(con, oid) == "expired"
    assert con.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0


def test_a_taker_waits_for_an_observation_that_exists_by_now(con):
    mid, _ = _kmarket(con)
    _book(con, mid, -1, [("0.4000", "500")])
    oid = _order(con, mid)
    _book(con, mid, 5, [("0.5500", "500")])
    paper.simulate(con, at(4))                   # the 5-minute book is in the future
    assert _status(con, oid) == "open"
    paper.simulate(con, at(5))
    assert _status(con, oid) == "filled"
    f = con.execute("SELECT * FROM paper_fills").fetchone()
    assert f["filled_at"] == store.canon_ts(at(5)) and f["price"] == 0.45


def test_a_taker_walks_levels_up_to_its_limit_and_the_rest_is_cancelled(con):
    mid, _ = _kmarket(con)
    _book(con, mid, -1, [("0.4000", "500")])
    oid = _order(con, mid, size=100, limit_price=0.47)
    # yes asks 0.45 x 30, 0.46 x 50, 0.48 x 500 (over the limit)
    _book(con, mid, 1, [("0.5500", "30"), ("0.5400", "50"), ("0.5200", "500")])
    t = paper.simulate(con, at(2))
    assert _status(con, oid) == "partial" and t["partial"] == 1
    fills = con.execute("SELECT price, contracts FROM paper_fills ORDER BY fill_id").fetchall()
    assert [tuple(f) for f in fills] == [(0.45, 30.0), (0.46, 50.0)]
    # It does not rest: a better later book changes nothing.
    _book(con, mid, 3, [("0.6000", "999")])
    paper.simulate(con, at(4))
    assert con.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 2


def test_a_maker_is_not_filled_by_the_market_merely_touching_its_price(con):
    mid, _ = _kmarket(con)
    _book(con, mid, -1, [("0.5000", "500")])
    oid = _order(con, mid, role="maker", size=50, limit_price=0.40,
                 expires_at=at(60))
    _book(con, mid, 1, [("0.6000", "500")])      # best yes ask = 0.40: a touch
    paper.simulate(con, at(2))
    assert _status(con, oid) == "open"
    _book(con, mid, 3, [("0.6100", "20")])       # 0.39: traded through, 20 shown
    paper.simulate(con, at(4))
    assert _status(con, oid) == "partial_open"
    f = con.execute("SELECT price, contracts FROM paper_fills").fetchone()
    assert tuple(f) == (0.40, 20.0)              # at its own price, the size shown
    _book(con, mid, 5, [("0.6200", "100")])
    paper.simulate(con, at(6))
    assert _status(con, oid) == "filled"
    assert con.execute("SELECT SUM(contracts) FROM paper_fills").fetchone()[0] == 50


def test_a_maker_expires(con):
    mid, _ = _kmarket(con)
    _book(con, mid, -1, [("0.5000", "500")])
    oid = _order(con, mid, role="maker", size=50, limit_price=0.40, expires_at=at(10))
    _book(con, mid, 20, [("0.7000", "500")])     # after it expired
    paper.simulate(con, at(30))
    assert _status(con, oid) == "expired"


def _game_book(con, when, away_ml):
    ev = [{"id": "g", "commence_time": START, "home_team": "H", "away_team": "A",
           "bookmakers": [{"key": "draftkings", "markets": [{"key": "h2h", "outcomes": [
               {"name": "A", "price": away_ml}, {"name": "H", "price": -170}]}]}]}]
    sportsbook.write(con, "nba", ev, when)
    return store.market_key("sportsbook:draftkings", "g", "h2h")


def test_a_pregame_order_never_fills_at_an_in_play_price(con):
    # Found by A-V4 on real data: the next price after a late order was an
    # in-play one. It must expire, not fill.
    mid = _game_book(con, "2026-11-04T00:00:00Z", 150)
    oid = _order(con, mid, outcome="away", size=100, limit_price=0.9,
                 now="2026-11-04T00:05:00Z")
    _game_book(con, "2026-11-04T00:20:00Z", 120)          # after the 00:10 start
    paper.simulate(con, "2026-11-04T00:30:00Z")
    assert _status(con, oid) == "expired"
    assert con.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0


def test_an_order_still_waiting_at_the_start_expires(con):
    mid = _game_book(con, "2026-11-04T00:00:00Z", 150)
    oid = _order(con, mid, outcome="away", size=100, limit_price=0.9,
                 now="2026-11-04T00:05:00Z")
    paper.simulate(con, "2026-11-04T00:08:00Z")
    assert _status(con, oid) == "open"                     # nothing yet, not started
    paper.simulate(con, "2026-11-04T00:10:00Z")
    assert _status(con, oid) == "expired"                  # the start: too late


def test_a_resting_order_stops_at_the_start(con):
    mid, _ = _kmarket(con)
    con.execute("UPDATE markets SET event_start=? WHERE market_id=?",
                (store.canon_ts(at(10)), mid))
    _book(con, mid, -1, [("0.5000", "500")])
    oid = _order(con, mid, role="maker", size=50, limit_price=0.40, expires_at=at(60))
    _book(con, mid, 20, [("0.7000", "500")])               # traded through, but in play
    paper.simulate(con, at(30))
    assert _status(con, oid) == "expired"


def test_an_order_on_a_market_with_no_price_yet_is_refused(con):
    mid, _ = _kmarket(con)
    with pytest.raises(Refused, match="no price"):
        _order(con, mid)


def test_a_book_order_fills_its_stake_at_the_next_price_shown(con):
    ev = [{"id": "g", "commence_time": START, "home_team": "H", "away_team": "A",
           "bookmakers": [{"key": "draftkings", "markets": [{"key": "h2h", "outcomes": [
               {"name": "A", "price": 150}, {"name": "H", "price": -170}]}]}]}]
    sportsbook.write(con, "nba", ev, at(-1))
    mid = store.market_key("sportsbook:draftkings", "g", "h2h")
    oid = _order(con, mid, outcome="away", size=100, limit_price=0.41)
    ev[0]["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = 145
    sportsbook.write(con, "nba", ev, at(1))
    paper.simulate(con, at(2))
    pos = con.execute("SELECT * FROM paper_positions WHERE order_id=?", (oid,)).fetchone()
    assert pos["avg_price"] == pytest.approx(1 / 2.45)       # +145, not the +150 before
    assert pos["stake"] == pytest.approx(100.0)
    assert pos["contracts"] == pytest.approx(245.0)          # pays $245 if it wins


# ---------------------------------------------------------------- refusals ---

def test_only_paper_and_placebo_orders_exist(con):
    mid, _ = _kmarket(con)
    _book(con, mid, -1, [("0.5000", "500")])
    for mode in ("real", "live", ""):
        with pytest.raises(Refused):
            _order(con, mid, mode=mode)
    _order(con, mid, mode="placebo")


def test_the_daily_exposure_cap_is_enforced_before_an_order_exists(con, monkeypatch):
    monkeypatch.setattr(config, "STRATEGY_DAILY_EXPOSURE", {"default": 60.0})
    mid, _ = _kmarket(con)
    _book(con, mid, -1, [("0.5000", "500")])
    _order(con, mid, size=100, limit_price=0.50)       # 50.00 + 0.0175 x 100 = 51.75
    with pytest.raises(Refused, match="daily exposure cap"):
        _order(con, mid, size=20, limit_price=0.50)    # 10.35 more: over 60
    assert con.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 1
    _order(con, mid, size=100, limit_price=0.50, mode="placebo")   # its own budget
    # Tomorrow (ET) starts again.
    _order(con, mid, size=100, limit_price=0.50, now=(T + dt.timedelta(days=1)).isoformat())


def test_an_order_whose_fees_cannot_be_priced_is_refused(con):
    mid, _ = _kmarket(con)
    _book(con, mid, -1, [("0.5000", "500")], fee="kalshi:flat:1")
    with pytest.raises(Refused, match="fees"):
        _order(con, mid)


def test_the_kill_switch_and_price_only_venues_refuse_orders(con, monkeypatch):
    mid, _ = _kmarket(con, sport="nfl")
    _book(con, mid, -1, [("0.5000", "500")], sport="nfl")
    monkeypatch.setattr(config, "KALSHI_SPORTS_ENABLED", False)
    with pytest.raises(Refused, match="switched off"):
        _order(con, mid)
    store.upsert_markets(con, [{"market_id": "pm", "venue": "polymarket",
                                "venue_market_id": "0x1", "canonical_event_id": "x",
                                "market_type": "binary", "first_seen": at(0)}])
    with pytest.raises(Refused, match="price source"):
        _order(con, "pm")


# ----------------------------------------------------------------- capital ---

def test_a_position_carries_its_stake_fee_days_and_annualized_ev(con):
    mid, _ = _kmarket(con)
    _book(con, mid, -1, [("0.4900", "500")], yes_bids=(("0.4900", "500"),))
    oid = _order(con, mid, size=100, limit_price=0.52)
    _book(con, mid, 1, [("0.5200", "500")], yes_bids=(("0.4600", "500"),))  # ask 0.48
    paper.simulate(con, at(1))
    p = con.execute("SELECT * FROM paper_positions WHERE order_id=?", (oid,)).fetchone()
    fee = 0.07 * 100 * 0.48 * 0.52                               # 1.7472
    assert p["fee"] == pytest.approx(round(fee + 0.0028, 2))     # cent rounding: 1.75
    assert p["stake"] == pytest.approx(48 + p["fee"])
    assert p["days_to_resolution"] == pytest.approx(
        capital.days_between(at(1), "2026-11-04T04:00:00Z"))
    # Fair value at the moment of the fill, from the book seen then: this
    # market has no books, so its own mid - bid 0.46, ask 0.48 - is 0.47.
    assert p["fair_source"] == "kalshi-mid" and p["fair_p"] == pytest.approx(0.47)
    assert p["ev"] == pytest.approx(0.47 / (p["stake"] / 100) - 1)     # taking the ask: < 0
    assert p["annualized_ev"] == pytest.approx(p["ev"] / p["days_to_resolution"] * 365)


def test_capital_locked_by_strategy_and_by_the_month_it_comes_back(con):
    mid, _ = _kmarket(con)
    _book(con, mid, -1, [("0.5000", "500")])
    for strat in ("a", "a", "b"):
        _order(con, mid, strategy=strat, size=10, limit_price=0.60)
    _book(con, mid, 1, [("0.5000", "500")])
    paper.simulate(con, at(2))
    lk = capital.locked(con)
    assert lk["total"] == pytest.approx(3 * (5 + 0.18))
    assert set(lk["by_strategy"]) == {"a", "b"}
    assert lk["by_month"] == {"2026-11": pytest.approx(lk["total"])}
    pid = con.execute("SELECT position_id FROM paper_positions LIMIT 1").fetchone()[0]
    pnl = paper.settle(con, pid, "win", at(300))
    assert pnl == pytest.approx(10 - 5.18)
    assert capital.locked(con)["total"] == pytest.approx(2 * 5.18)


def test_annualizing_says_what_a_long_contract_is_worth():
    assert capital.annualize(0.05, 240) == pytest.approx(0.05 / 240 * 365)   # ~7.6%
    assert capital.annualize(0.05, 0) is None
    assert capital.annualize(None, 10) is None
