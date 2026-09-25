"""fair_value(): the one fair price. Precedence, no look-ahead, no in-play
prices, staleness, and the mapping that prices an exchange contract from the
books."""
import pytest

import config
import db
from bets.engine import novig_probs
from scanner import store
from scanner.fair import fair_value
from scanner.venues import kalshi, sportsbook

START = "2026-09-28T00:20:00Z"
PULL1, PULL2 = "2026-09-27T20:00:00+00:00", "2026-09-27T23:50:00+00:00"
INPLAY = "2026-09-28T00:25:00+00:00"


def _event(books):
    """books: {key: (away_ml, home_ml)} plus optional ('spread', line) entries."""
    bms = []
    for key, prices in books.items():
        markets = [{"key": "h2h", "outcomes": [
            {"name": "Atlanta Falcons", "price": prices[0]},
            {"name": "Green Bay Packers", "price": prices[1]}]}]
        if len(prices) == 4:
            a, h, line, _ = prices
            markets.append({"key": "spreads", "outcomes": [
                {"name": "Atlanta Falcons", "price": -110, "point": -line},
                {"name": "Green Bay Packers", "price": -110, "point": line}]})
        bms.append({"key": key, "markets": markets})
    return [{"id": "g1", "commence_time": START, "home_team": "Green Bay Packers",
             "away_team": "Atlanta Falcons", "bookmakers": bms}]


def _h2h(book):
    return store.market_key(f"sportsbook:{book}", "g1", "h2h")


@pytest.fixture
def con(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init()
    capsys.readouterr()
    c = db.connect()
    yield c
    c.close()


def test_pinnacle_answers_when_it_priced_the_pull(con):
    sportsbook.write(con, "nfl", _event({"pinnacle": (205, -230),
                                         "draftkings": (200, -245)}), PULL1)
    fv = fair_value(con, _h2h("draftkings"), "home", PULL1)
    assert fv["source"] == "pinnacle"
    assert fv["p"] == novig_probs(205, -230)[1]
    assert fv["books"] == ["sportsbook:pinnacle"] and fv["age_s"] == 0


def test_without_pinnacle_it_is_the_mean_of_every_book(con):
    sportsbook.write(con, "nfl", _event({"draftkings": (200, -245),
                                         "fanduel": (210, -250)}), PULL1)
    fv = fair_value(con, _h2h("draftkings"), "away", PULL1)
    want = (novig_probs(200, -245)[0] + novig_probs(210, -250)[0]) / 2
    assert fv["source"] == "consensus" and fv["p"] == pytest.approx(want, abs=1e-15)


def test_nothing_after_the_moment_is_read(con):
    sportsbook.write(con, "nfl", _event({"pinnacle": (205, -230)}), PULL1)
    sportsbook.write(con, "nfl", _event({"pinnacle": (150, -170)}), PULL2)
    between = "2026-09-27T22:00:00+00:00"
    fv = fair_value(con, _h2h("pinnacle"), "home", between)
    assert fv["as_of"] == store.canon_ts(PULL1)
    assert fv["p"] == novig_probs(205, -230)[1]
    assert fv["age_s"] == 7200
    assert fair_value(con, _h2h("pinnacle"), "home", "2026-09-27T19:59:59+00:00") is None


def test_an_in_play_price_is_never_a_fair_price(con):
    sportsbook.write(con, "nfl", _event({"pinnacle": (205, -230)}), PULL2)
    sportsbook.write(con, "nfl", _event({"pinnacle": (1500, -10000)}), INPLAY)
    fv = fair_value(con, _h2h("pinnacle"), "home", INPLAY)
    assert fv["as_of"] == store.canon_ts(PULL2)
    # A pull exactly at the start is in play too.
    sportsbook.write(con, "nfl", _event({"pinnacle": (900, -2000)}), START)
    assert fair_value(con, _h2h("pinnacle"), "home", INPLAY)["as_of"] == store.canon_ts(PULL2)


def test_a_rain_delay_does_not_turn_pregame_prices_into_in_play_ones(con):
    # Found by A-V1 on real data: a game's reported start moved from 18:11 to
    # 19:11 to 19:41 UTC pull by pull. A price at 18:25 was still pregame.
    ev = _event({"pinnacle": (205, -230)})
    ev[0]["commence_time"] = "2026-09-20T18:11:00Z"
    sportsbook.write(con, "nfl", ev, "2026-09-20T17:50:00+00:00")
    ev = _event({"pinnacle": (150, -170)})
    ev[0]["commence_time"] = "2026-09-20T19:11:00Z"
    sportsbook.write(con, "nfl", ev, "2026-09-20T18:25:00+00:00")
    fv = fair_value(con, _h2h("pinnacle"), "home", "2026-09-20T18:25:00+00:00")
    assert fv["as_of"] == "2026-09-20T18:25:00.000000+00:00"
    assert fv["p"] == novig_probs(150, -170)[1]


def test_the_archive_conversion_keeps_the_latest_reported_start():
    snap = {"id": 1, "game_id": "mlb-abc", "book": "pinnacle", "away_ml": 110,
            "home_ml": -120, "commence_time": "2026-09-20T18:11:00Z",
            "ts": "2026-09-20T17:50:00+00:00"}
    later = dict(snap, id=2, commence_time="2026-09-20T19:41:00Z",
                 ts="2026-09-20T19:22:00+00:00")
    markets, _ = sportsbook.from_snapshots([snap, later])
    assert markets[0]["event_start"] == "2026-09-20T19:41:00.000000+00:00"
    assert markets[0]["first_seen"] == "2026-09-20T17:50:00.000000+00:00"


def test_a_stale_price_is_refused_when_the_caller_says_so(con):
    sportsbook.write(con, "nfl", _event({"pinnacle": (205, -230)}), PULL1)
    at = "2026-09-27T20:30:00+00:00"
    assert fair_value(con, _h2h("pinnacle"), "home", at, max_age_s=3600)["p"]
    assert fair_value(con, _h2h("pinnacle"), "home", at, max_age_s=600) is None


def test_a_spread_is_priced_only_from_books_at_the_same_line(con):
    sportsbook.write(con, "nfl", _event({"pinnacle": (205, -230, -3.0, 0),
                                         "draftkings": (200, -245, -3.5, 0),
                                         "fanduel": (210, -250, -3.5, 0)}), PULL1)
    dk = store.market_key("sportsbook:draftkings", "g1", "spread", -3.5)
    fv = fair_value(con, dk, "home", PULL1)
    assert fv["source"] == "consensus"          # Pinnacle is at -3, not -3.5
    assert set(fv["books"]) == {"sportsbook:draftkings", "sportsbook:fanduel"}


def test_a_book_with_only_one_side_is_ignored(con):
    ev = _event({"pinnacle": (205, -230)})
    sportsbook.write(con, "nfl", ev, PULL1)
    # Drop Pinnacle's home price from the store: it cannot be de-vigged.
    con.execute("DELETE FROM prices WHERE venue='sportsbook:pinnacle' AND outcome='home'")
    assert fair_value(con, _h2h("pinnacle"), "away", PULL1) is None


def _kalshi(con, yes_outcome="home", sport="nfl"):
    row = kalshi.market_row("KXNFLGAME-GB", canonical_event_id="nfl-g1",
                            first_seen=PULL1, sport=sport, market_type="h2h",
                            yes_outcome=yes_outcome, event_start=START)
    store.upsert_markets(con, [row])
    book = {"orderbook_fp": {"yes_dollars": [["0.6800", "10"]],
                             "no_dollars": [["0.3000", "10"]]}}
    store.insert_prices(con, kalshi.price_rows(row["market_id"], book, PULL1,
                                               "kalshi:quadratic:1", sport=sport))
    return row["market_id"]


def test_a_kalshi_contract_is_priced_from_the_books_through_yes_outcome(con):
    sportsbook.write(con, "nfl", _event({"pinnacle": (205, -230)}), PULL1)
    k = _kalshi(con, yes_outcome="home")
    p_away, p_home = novig_probs(205, -230)
    assert fair_value(con, k, "yes", PULL1)["p"] == p_home
    assert fair_value(con, k, "no", PULL1)["p"] == p_away
    assert fair_value(con, k, "yes", PULL1)["source"] == "pinnacle"


def test_with_no_books_an_exchange_falls_back_to_its_own_mid(con):
    k = _kalshi(con, yes_outcome=None)
    fv = fair_value(con, k, "yes", PULL1)
    assert fv["source"] == "kalshi-mid"
    assert fv["p"] == pytest.approx((0.68 + 0.70) / 2)    # bid 0.68, ask 1 - 0.30


def test_the_kill_switch_takes_kalshi_sports_out_of_fair_value(con, monkeypatch):
    sportsbook.write(con, "nfl", _event({"pinnacle": (205, -230)}), PULL1)
    k = _kalshi(con)
    monkeypatch.setattr(config, "KALSHI_SPORTS_ENABLED", False)
    assert fair_value(con, k, "yes", PULL1) is None
    assert fair_value(con, _h2h("pinnacle"), "home", PULL1) is not None   # books unaffected
