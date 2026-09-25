"""Venue adapters: each venue's native payload into the one price schema.

Payload shapes are copied from each venue's documentation (read 2026-09-25)
and, for the sportsbook, from a real collected Odds API slate."""
import pytest

import config
import db
from bets.engine import american_to_decimal
from ingest.quality import Rejects
from scanner import store, venues
from scanner.venues import kalshi, polymarket, sportsbook

T0 = "2026-09-27T15:00:00+00:00"


def _event(**over):
    ev = {"id": "e5fb", "commence_time": "2026-09-28T00:20:00Z",
          "home_team": "Green Bay Packers", "away_team": "Atlanta Falcons",
          "bookmakers": [
              {"key": "pinnacle", "last_update": "2026-09-27T14:59:00Z",
               "markets": [
                   {"key": "h2h", "last_update": "2026-09-27T14:58:00Z",
                    "outcomes": [{"name": "Atlanta Falcons", "price": 205},
                                 {"name": "Green Bay Packers", "price": -230}]},
                   {"key": "spreads", "outcomes": [
                       {"name": "Atlanta Falcons", "price": -108, "point": 5.5},
                       {"name": "Green Bay Packers", "price": -104, "point": -5.5}]},
                   {"key": "totals", "outcomes": [
                       {"name": "Over", "price": -105, "point": 47.5},
                       {"name": "Under", "price": -107, "point": 47.5}]}]},
              {"key": "draftkings", "markets": [
                  {"key": "h2h", "outcomes": [
                      {"name": "Atlanta Falcons", "price": 200},
                      {"name": "Green Bay Packers", "price": -245}]}]}]}
    ev.update(over)
    return ev


@pytest.fixture
def con(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init()
    capsys.readouterr()
    c = db.connect()
    yield c
    c.close()


# ------------------------------------------------------------- sportsbook ---

def test_a_book_event_becomes_one_market_per_book_type_and_line():
    games, markets, prices = sportsbook.rows("nfl", [_event()], T0)
    assert [g["game_id"] for g in games] == ["nfl-e5fb"]
    kinds = sorted((m["venue"], m["market_type"], m["line"]) for m in markets)
    assert kinds == [("sportsbook:draftkings", "h2h", None),
                     ("sportsbook:pinnacle", "h2h", None),
                     ("sportsbook:pinnacle", "spread", -5.5),     # the HOME point
                     ("sportsbook:pinnacle", "total", 47.5)]
    assert all(m["canonical_event_id"] == "nfl-e5fb" for m in markets)
    assert len(prices) == 8                                          # two sides each


def test_a_book_price_is_one_over_decimal_with_the_native_kept():
    _, _, prices = sportsbook.rows("nfl", [_event()], T0)
    p = next(x for x in prices if x["venue"] == "sportsbook:pinnacle"
             and x["outcome"] == "away" and x["market_id"].endswith("|h2h|"))
    assert p["price_native"] == "205"
    assert p["price"] == pytest.approx(1 / american_to_decimal(205))
    assert p["quote"] == "ask" and p["size_available"] is None
    assert p["fee_model"] == "book"
    assert p["source_last_update"] == "2026-09-27T14:58:00.000000+00:00"


def test_book_rows_that_cannot_be_true_are_counted_not_guessed():
    bad = _event()
    bad["bookmakers"][0]["markets"][1]["outcomes"][0]["point"] = 6.5   # not a mirror
    bad["bookmakers"][1]["markets"][0]["outcomes"][0]["name"] = "Someone Else"
    r = Rejects()
    _, markets, _ = sportsbook.rows("nfl", [bad], T0, rejects=r)
    assert len(r) == 2
    assert not any(m["market_type"] == "spread" for m in markets)
    assert not any(m["venue"] == "sportsbook:draftkings" for m in markets)


@pytest.mark.parametrize("point", [None, "N/A", "", "nan", "absent"])
def test_a_line_without_a_numeric_point_is_counted_not_raised(con, point):
    # One bad market used to raise out of rows() and lose the whole pull,
    # every clean game in it included, after its credits were spent.
    bad = _event()
    for o in bad["bookmakers"][0]["markets"][2]["outcomes"]:          # totals
        if point == "absent":
            del o["point"]
        else:
            o["point"] = point
    bad["bookmakers"][0]["markets"][1]["outcomes"][0]["point"] = "x"  # spreads
    r = Rejects()
    got = sportsbook.write(con, "nfl", [bad, _event(id="c1ea")], T0, rejects=r)
    assert len(r) == 2
    assert got["games"] == 2 and got["markets"] == 2 + 4 and got["prices"] == 12


def test_write_stores_games_markets_prices_and_dedupes(con):
    got = sportsbook.write(con, "nfl", [_event()], T0)
    assert got == {"games": 1, "markets": 4, "prices": 8, "rejected": 0}
    again = sportsbook.write(con, "nfl", [_event()], T0)
    assert again["prices"] == 0                                     # same moment twice
    g = con.execute("SELECT * FROM games WHERE game_id='nfl-e5fb'").fetchone()
    assert g["game_date"] == "2026-09-27"                           # the ET date
    stamps = {r[0] for r in con.execute("SELECT captured_at FROM prices")}
    assert stamps == {"2026-09-27T15:00:00.000000+00:00"}


def test_an_impossible_price_is_rejected_with_the_ingest_rule(con):
    bad = _event()
    bad["bookmakers"][1]["markets"][0]["outcomes"][0]["price"] = -50
    r = Rejects()
    got = sportsbook.write(con, "nfl", [bad], T0, rejects=r)
    assert got["prices"] == 7
    assert any("impossible" in why for why in r.reasons)


# ------------------------------------------------------------------ Kalshi ---

BOOK = {"orderbook_fp": {
    # Deliberately NOT sorted best-first: the adapter must not trust order.
    "yes_dollars": [["0.4300", "50.00"], ["0.4500", "120.00"], ["0.4400", "10.00"],
                    ["0.4000", "5.00"]],
    "no_dollars": [["0.5300", "40.00"], ["0.5200", "70.00"]]}}
KM = "kalshi:quadratic:1"


def test_kalshi_asks_are_the_other_sides_bids_flipped():
    rows = kalshi.price_rows("k1", BOOK, T0, KM, sport="nfl")
    yes_ask = [r for r in rows if r["outcome"] == "yes" and r["quote"] == "ask"]
    assert [(r["level"], r["price"], r["size_available"]) for r in yes_ask] == [
        (1, 0.47, 40.0), (2, 0.48, 70.0)]                   # 1 - 0.53, 1 - 0.52
    assert yes_ask[0]["price_native"] == "1-no_bid:0.5300"
    no_ask = [r for r in rows if r["outcome"] == "no" and r["quote"] == "ask"]
    assert [r["price"] for r in no_ask] == [0.55, 0.56, 0.57]   # top three only
    yes_bid = [r for r in rows if r["outcome"] == "yes" and r["quote"] == "bid"]
    assert [r["price"] for r in yes_bid] == [0.45, 0.44, 0.43]


def test_kalshi_rows_store_cleanly(con):
    store.insert_prices(con, kalshi.price_rows("k1", BOOK, T0, KM, sport="nfl"))
    assert con.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 10


def test_kalshi_kill_switch_stops_sports_and_only_sports(monkeypatch):
    monkeypatch.setattr(config, "KALSHI_SPORTS_ENABLED", False)
    assert kalshi.price_rows("k1", BOOK, T0, KM, sport="nfl") == []
    with pytest.raises(PermissionError):
        kalshi.market_row("KXNFL-X", canonical_event_id="nfl-e5fb",
                          first_seen=T0, sport="nfl")
    assert venues.paper_allowed("kalshi", "nfl")[0] is False
    # A weather market is not a sports market.
    assert len(kalshi.price_rows("k2", BOOK, T0, KM, sport=None)) == 10
    assert venues.allowed("kalshi", None) == (True, None)


def test_the_kill_switch_cannot_be_skipped_by_leaving_sport_out(monkeypatch):
    # sport=None means "not a sports market", which the switch lets through,
    # so a caller has to say it: an NFL contract built without it was stored,
    # priced and paper-filled with the switch off.
    monkeypatch.setattr(config, "KALSHI_SPORTS_ENABLED", False)
    with pytest.raises(TypeError):
        kalshi.market_row("KXNFL-X", canonical_event_id="nfl-e5fb", first_seen=T0,
                          market_type="h2h", yes_outcome="home")
    with pytest.raises(TypeError):
        kalshi.price_rows("k1", BOOK, T0, KM)


def test_kalshi_needs_its_documented_shape():
    with pytest.raises(ValueError):
        kalshi.price_rows("k1", {"orderbook": {"yes": [[45, 10]]}}, T0, KM, sport=None)


# -------------------------------------------------------------- Polymarket ---

PM = {"market": "0xbd", "asset_id": "5211", "timestamp": "1790000000000",
      "bids": [{"price": "0.47", "size": "2500"}, {"price": "0.48", "size": "1000"}],
      "asks": [{"price": "0.53", "size": "1500"}, {"price": "0.52", "size": "800"}]}


def test_polymarket_best_bid_is_highest_and_best_ask_lowest():
    rows = polymarket.price_rows("p1", "yes", PM, T0, "polymarket:0.05")
    best = {r["quote"]: r for r in rows if r["level"] == 1}
    assert best["bid"]["price"] == 0.48 and best["ask"]["price"] == 0.52
    assert best["ask"]["size_available"] == 800.0
    assert best["ask"]["source_last_update"].startswith("2026-09-21T")


def test_polymarket_is_a_price_source_and_never_executes():
    assert venues.paper_allowed("polymarket", None)[0] is False
    assert venues.paper_allowed("kalshi", None)[0] is True
    assert venues.paper_allowed("sportsbook:draftkings", "nfl")[0] is True


def test_unknown_venues_are_refused():
    for bad in ("sportsbook:", "draftkings", "kalshi:x", "predictit"):
        with pytest.raises(ValueError):
            venues.family(bad)


# ------------------------------------------------------------------ store ---

def test_every_stored_time_has_one_shape():
    assert store.canon_ts("2026-09-27T23:20:00Z") == "2026-09-27T23:20:00.000000+00:00"
    assert store.canon_ts("2026-09-27T19:20:00-04:00") == "2026-09-27T23:20:00.000000+00:00"
    assert store.canon_ts("2026-09-27T23:20:00") == "2026-09-27T23:20:00.000000+00:00"
    with pytest.raises(ValueError):
        store.canon_ts("yesterday")


def test_a_start_moves_later_only_if_reported_before_the_new_start(con):
    # paper.simulate and grade read this stored start. A delay announced
    # ahead of time moves it; a later start first reported after that time
    # (the game was under way) does not; an earlier start always wins.
    row = kalshi.market_row("KXG", canonical_event_id="nfl-e5fb", first_seen=T0,
                            sport=None, event_start="2026-09-28T00:20:00Z")

    def sighting(seen, start):
        store.upsert_markets(con, [dict(row, first_seen=seen, event_start=start)])
        return store.market(con, row["market_id"])["event_start"]
    assert sighting(T0, "2026-09-28T00:20:00Z") == "2026-09-28T00:20:00.000000+00:00"
    assert (sighting("2026-09-28T00:10:00Z", "2026-09-28T01:05:00Z")      # delay
            == "2026-09-28T01:05:00.000000+00:00")
    assert (sighting("2026-09-28T02:00:00Z", "2026-09-28T01:30:00Z")      # too late
            == "2026-09-28T01:05:00.000000+00:00")
    assert (sighting("2026-09-28T02:00:00Z", "2026-09-28T00:50:00Z")      # earlier
            == "2026-09-28T00:50:00.000000+00:00")
    assert sighting("2026-09-28T02:05:00Z", None) == "2026-09-28T00:50:00.000000+00:00"


def test_a_malformed_fee_key_is_refused_but_an_unread_one_is_kept():
    row = {"market_id": "k1", "venue": "kalshi", "outcome": "yes", "quote": "ask",
           "level": 1, "price": 0.5, "price_native": "0.5000",
           "fee_model": "kalshi:flat:1", "captured_at": T0}
    assert store.check_price(row) is None             # fee unread, price still kept
    assert store.check_price(dict(row, fee_model="kalshi")) is not None
    assert store.check_price(dict(row, price=1.0)) is not None
    assert store.check_price(dict(row, quote="mid")) is not None
