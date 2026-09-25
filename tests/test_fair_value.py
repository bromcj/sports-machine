"""fair_value(): the one fair price. Precedence, no look-ahead, no in-play
prices, staleness, and the mapping that prices an exchange contract from the
books."""
import pytest

import config
import db
from bets.engine import novig_probs
from scanner import store
from scanner.fair import fair_value, game_start
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


def test_a_start_reported_later_after_the_game_began_does_not_reopen_it(con):
    # mlb-3ccd92ca, 2024-04-27 (Astros @ Rockies): first pitch 22:05Z. The
    # 22:25 pull still said 22:05 and carried in-play prices; pulls from 23:55
    # re-reported the start as 22:26:59. That report came after 22:26:59, so it
    # must not turn the 22:25 in-play pull into a pregame one.
    def pull(when, start, books):
        ev = _event(books)
        ev[0]["commence_time"] = start
        sportsbook.write(con, "mlb", ev, when)
    pull("2024-04-27T21:20:00+00:00", "2024-04-27T22:05:00Z",
         {"pinnacle": (-208, 188), "draftkings": (-218, 180)})
    pull("2024-04-27T22:25:00+00:00", "2024-04-27T22:05:00Z",
         {"pinnacle": (-212, 192), "draftkings": (-145, 114)})
    before = fair_value(con, _h2h("draftkings"), "away", "2024-04-27T22:30:00+00:00")
    pull("2024-04-27T23:55:00+00:00", "2024-04-27T22:26:59Z",
         {"draftkings": (-1150, 650)})
    pull("2024-04-28T00:20:00+00:00", "2024-04-27T22:26:59Z",
         {"pinnacle": (-1067, 733), "draftkings": (-1750, 850)})
    after = fair_value(con, _h2h("draftkings"), "away", "2024-04-27T22:30:00+00:00")
    assert before["as_of"] == after["as_of"] == "2024-04-27T21:20:00.000000+00:00"
    assert after["p"] == before["p"]


def _pull(con, when, start, books):
    ev = _event(books)
    ev[0]["commence_time"] = start
    sportsbook.write(con, "mlb", ev, when)


def test_a_book_that_stops_quoting_before_a_delay_does_not_drag_the_start_back(con):
    # Royals @ Twins, 2026-06-05: Pinnacle's last pull said 00:16; the delay
    # was reported only in pulls Pinnacle was no longer in (01:06, then 01:31).
    # The earliest start over the books kept answering with Pinnacle's price
    # from 23:55, an hour and more old, and A-V1 went red on that week.
    _pull(con, "2026-06-05T23:55:00+00:00", "2026-06-06T00:16:00Z",
          {"pinnacle": (102, -110), "draftkings": (100, -121)})
    _pull(con, "2026-06-06T00:55:00+00:00", "2026-06-06T01:06:00Z",
          {"draftkings": (100, -121)})
    for via in ("draftkings", "pinnacle"):
        fv = fair_value(con, _h2h(via), "home", "2026-06-06T00:55:00+00:00")
        assert fv["as_of"] == "2026-06-06T00:55:00.000000+00:00", (via, fv)
        assert fv["source"] == "consensus" and fv["p"] == novig_probs(100, -121)[1]


def test_a_book_last_seen_before_an_earlier_start_is_not_trusted_either(con):
    # Orioles @ Reds, 2024-05-03: the 00:55 pull still said 01:10; the next
    # pull (Pinnacle gone) said first pitch was 00:50:51. Pinnacle's 00:55
    # price was in play, whichever book's market is asked - so the start is
    # not each book's own, nor the latest over the books.
    _pull(con, "2024-05-03T23:25:00+00:00", "2024-05-03T23:40:00Z",
          {"pinnacle": (105, -114), "draftkings": (100, -120)})
    _pull(con, "2024-05-04T00:55:00+00:00", "2024-05-04T01:10:00Z",
          {"pinnacle": (100, -108), "draftkings": (-105, -115)})
    _pull(con, "2024-05-04T01:25:00+00:00", "2024-05-04T00:50:51Z",
          {"draftkings": (-105, -125)})
    for via in ("draftkings", "pinnacle"):
        fv = fair_value(con, _h2h(via), "home", "2024-05-04T00:55:00+00:00")
        assert fv["as_of"] == "2024-05-03T23:25:00.000000+00:00", (via, fv)


def test_books_priced_together_that_disagree_on_the_start_take_the_earliest(con):
    # Pinnacle missed the pull that moved the start EARLIER, to 18:30; the
    # 18:50 pull then said 18:45, after 18:45, which DraftKings rightly
    # ignores and Pinnacle takes (earlier than its 19:00). Both were last
    # priced at 18:50. The 18:40 pull is in play by the feed's own word.
    _pull(con, "2026-09-20T18:00:00+00:00", "2026-09-20T19:00:00Z",
          {"pinnacle": (205, -230), "draftkings": (200, -245)})
    _pull(con, "2026-09-20T18:20:00+00:00", "2026-09-20T18:30:00Z",
          {"draftkings": (190, -230)})
    _pull(con, "2026-09-20T18:40:00+00:00", "2026-09-20T18:30:00Z",
          {"draftkings": (900, -2000)})
    _pull(con, "2026-09-20T18:50:00+00:00", "2026-09-20T18:45:00Z",
          {"pinnacle": (1500, -10000), "draftkings": (1400, -9000)})
    for via in ("draftkings", "pinnacle"):
        fv = fair_value(con, _h2h(via), "home", "2026-09-20T18:40:00+00:00")
        assert fv["as_of"] == "2026-09-20T18:20:00.000000+00:00", (via, fv)


def test_a_delay_announced_while_a_book_was_missing_still_moves_its_start(con):
    # mlb-746c8fcb, 2024-04-03: the start moved 17:05 -> 19:00 -> 19:30 ->
    # 22:05, each announced before it passed, but DraftKings was not in the
    # 22:00 pull that announced 22:05. It came back at 22:55 saying 22:05,
    # after 22:05 had passed. Judged book by book that late report was
    # refused, DraftKings kept 19:30, and the game's start fell back to it:
    # the pregame 22:00 pull was answered with the 19:20 one. A game has one
    # start; every book's market carries it.
    _pull(con, "2024-04-03T16:20:00+00:00", "2024-04-03T17:05:00Z",
          {"pinnacle": (140, -163), "draftkings": (142, -170)})
    _pull(con, "2024-04-03T18:52:00+00:00", "2024-04-03T19:00:00Z",
          {"pinnacle": (147, -161), "draftkings": (140, -166)})
    _pull(con, "2024-04-03T19:20:00+00:00", "2024-04-03T19:30:00Z",
          {"pinnacle": (154, -168), "draftkings": (145, -175)})
    _pull(con, "2024-04-03T22:00:00+00:00", "2024-04-03T22:05:00Z",
          {"pinnacle": (153, -167)})
    _pull(con, "2024-04-03T22:55:00+00:00", "2024-04-03T22:05:00Z",
          {"pinnacle": (160, -180), "draftkings": (150, -175)})
    for via in ("draftkings", "pinnacle"):
        assert game_start(con, store.market(con, _h2h(via))) == (
            "2024-04-03T22:05:00.000000+00:00"), via
        fv = fair_value(con, _h2h(via), "home", "2024-04-03T22:00:00+00:00")
        assert fv["as_of"] == "2024-04-03T22:00:00.000000+00:00", (via, fv)
        assert fv["p"] == novig_probs(153, -167)[1]
    # The archive conversion reaches the same start for every book.
    snaps = []
    for i, (ts, start, books) in enumerate((
            ("2024-04-03T16:20:00+00:00", "2024-04-03T17:05:00Z", ("pinnacle", "draftkings")),
            ("2024-04-03T18:52:00+00:00", "2024-04-03T19:00:00Z", ("pinnacle", "draftkings")),
            ("2024-04-03T22:00:00+00:00", "2024-04-03T22:05:00Z", ("pinnacle",)),
            ("2024-04-03T22:55:00+00:00", "2024-04-03T22:05:00Z", ("pinnacle", "draftkings")))):
        snaps += [{"id": 10 * i + j, "game_id": "mlb-abc", "book": b, "away_ml": 150,
                   "home_ml": -170, "commence_time": start, "ts": ts}
                  for j, b in enumerate(books)]
    markets, _ = sportsbook.from_snapshots(snaps)
    assert {m["event_start"] for m in markets} == {"2024-04-03T22:05:00.000000+00:00"}


def test_game_start_is_the_start_fair_value_cuts_off_at(con):
    # The one public rule, so paper's fills and grading can judge "pregame"
    # by the same start. Royals @ Twins, reduced: the books' latest word is
    # 01:06, whichever book's market is asked, not Pinnacle's stale 00:16.
    _pull(con, "2026-06-05T23:55:00+00:00", "2026-06-06T00:16:00Z",
          {"pinnacle": (102, -110), "draftkings": (100, -121)})
    _pull(con, "2026-06-06T00:55:00+00:00", "2026-06-06T01:06:00Z",
          {"draftkings": (100, -121)})
    start = "2026-06-06T01:06:00.000000+00:00"
    for via in ("pinnacle", "draftkings"):
        assert game_start(con, store.market(con, _h2h(via))) == start
    # A price captured at that start is not read; the one before it is.
    _pull(con, start, "2026-06-06T01:06:00Z", {"draftkings": (900, -2000)})
    assert game_start(con, store.market(con, _h2h("draftkings"))) == start
    for at in (start, "2026-06-06T01:30:00+00:00"):
        assert fair_value(con, _h2h("draftkings"), "home", at)["as_of"] == (
            "2026-06-06T00:55:00.000000+00:00")
    # An exchange contract on the game: its own start caps the books' only
    # when earlier. Unmapped with no start of its own: nothing says.
    for own, want in (("2026-06-06T00:50:00Z", "2026-06-06T00:50:00.000000+00:00"),
                      ("2026-06-06T02:00:00Z", start)):
        row = kalshi.market_row(f"KX-{own}", canonical_event_id="mlb-g1",
                                first_seen="2026-06-05T20:00:00Z", sport="mlb",
                                market_type="h2h", yes_outcome="home", event_start=own)
        store.upsert_markets(con, [row])
        assert game_start(con, store.market(con, row["market_id"])) == want
    weather = kalshi.market_row("KXHIGHNY", canonical_event_id="weather:KNYC",
                                first_seen="2026-06-05T20:00:00Z", sport=None)
    store.upsert_markets(con, [weather])
    assert game_start(con, store.market(con, weather["market_id"])) is None


def test_the_archive_conversion_ignores_a_start_reported_after_it():
    snap = {"id": 1, "game_id": "mlb-abc", "book": "pinnacle", "away_ml": 110,
            "home_ml": -120, "commence_time": "2024-04-27T22:05:00Z",
            "ts": "2024-04-27T22:25:00+00:00"}
    late = dict(snap, id=2, commence_time="2024-04-27T22:26:59Z",
                ts="2024-04-27T23:55:00+00:00")
    for order in ([snap, late], [late, snap]):
        markets, _ = sportsbook.from_snapshots(order)
        assert markets[0]["event_start"] == "2024-04-27T22:05:00.000000+00:00"


def test_a_later_start_reported_at_its_own_moment_has_already_passed(con):
    # "Only if reported BEFORE it passed": reported at the very instant it
    # names, it is refused, like any report after it. Taken, the 19:10 pull
    # (in play, and still saying 19:00) became a pregame price.
    ts = "2026-09-20T19:30:00.000000+00:00"
    assert sportsbook.next_start("2026-09-20T19:00:00.000000+00:00", ts, ts) == (
        "2026-09-20T19:00:00.000000+00:00")
    _pull(con, "2026-09-20T18:00:00+00:00", "2026-09-20T19:00:00Z",
          {"pinnacle": (205, -230)})
    _pull(con, "2026-09-20T19:10:00+00:00", "2026-09-20T19:00:00Z",
          {"pinnacle": (900, -2000)})
    _pull(con, ts, "2026-09-20T19:30:00Z", {"pinnacle": (1500, -10000)})
    assert game_start(con, store.market(con, _h2h("pinnacle"))) == (
        "2026-09-20T19:00:00.000000+00:00")
    fv = fair_value(con, _h2h("pinnacle"), "home", "2026-09-20T19:20:00+00:00")
    assert fv["as_of"] == "2026-09-20T18:00:00.000000+00:00"


def test_the_archive_conversion_judges_pulls_in_capture_order():
    # A delay to 22:30 announced at 22:25, before it passed, after a 20:00
    # pull that said 22:05. In capture order the start is 22:30. Taken in the
    # order given, the 22:05 report came second and, being earlier, won.
    early = {"id": 1, "game_id": "mlb-abc", "book": "pinnacle", "away_ml": 110,
             "home_ml": -120, "commence_time": "2026-09-20T22:05:00Z",
             "ts": "2026-09-20T20:00:00+00:00"}
    delay = dict(early, id=2, commence_time="2026-09-20T22:30:00Z",
                 ts="2026-09-20T22:25:00+00:00")
    for order in ([early, delay], [delay, early]):
        markets, _ = sportsbook.from_snapshots(order)
        assert markets[0]["event_start"] == "2026-09-20T22:30:00.000000+00:00"


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


@pytest.mark.parametrize("outcome", ["yes", "no", "draw", "Home", "Away", "", "over"])
def test_an_outcome_a_book_market_does_not_have_gets_none(con, outcome):
    # Not the home side's number, which is what any unknown name used to get.
    sportsbook.write(con, "nfl", _event({"pinnacle": (205, -230)}), PULL1)
    assert fair_value(con, _h2h("pinnacle"), outcome, PULL1) is None


def test_a_total_is_over_or_under_and_nothing_else(con):
    ev = _event({"pinnacle": (205, -230)})
    ev[0]["bookmakers"][0]["markets"].append({"key": "totals", "outcomes": [
        {"name": "Over", "price": -120, "point": 44.5},
        {"name": "Under", "price": 100, "point": 44.5}]})
    sportsbook.write(con, "nfl", ev, PULL1)
    tot = store.market_key("sportsbook:pinnacle", "g1", "total", 44.5)
    assert fair_value(con, tot, "over", PULL1)["p"] == novig_probs(-120, 100)[0]
    for bad in ("home", "Over", "yes", ""):          # 'Over' is the feed's spelling
        assert fair_value(con, tot, bad, PULL1) is None


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


@pytest.mark.parametrize("yes", ["Away", "over", "draw"])
def test_a_yes_outcome_the_books_do_not_have_is_not_priced_from_them(con, yes):
    # yes_outcome='Away' used to price YES as the HOME side, from Pinnacle.
    sportsbook.write(con, "nfl", _event({"pinnacle": (205, -230)}), PULL1)
    k = _kalshi(con, yes_outcome=yes)
    for side in ("yes", "no"):
        assert fair_value(con, k, side, PULL1)["source"] == "kalshi-mid"


def test_with_no_books_an_exchange_falls_back_to_its_own_mid(con):
    k = _kalshi(con, yes_outcome=None)
    fv = fair_value(con, k, "yes", PULL1)
    assert fv["source"] == "kalshi-mid"
    assert fv["p"] == pytest.approx((0.68 + 0.70) / 2)    # bid 0.68, ask 1 - 0.30


LATE_BOOK = {"orderbook_fp": {"yes_dollars": [["0.9500", "10"]],
                              "no_dollars": [["0.0300", "10"]]}}


def test_the_exchange_mid_reads_nothing_after_the_moment(con):
    k = _kalshi(con, yes_outcome=None)                      # mid .69 at PULL1
    store.insert_prices(con, kalshi.price_rows(k, LATE_BOOK, PULL2,
                                               "kalshi:quadratic:1", sport="nfl"))
    for at in (PULL1, "2026-09-27T22:00:00+00:00"):
        fv = fair_value(con, k, "yes", at)
        assert fv["as_of"] == store.canon_ts(PULL1)
        assert fv["p"] == pytest.approx((0.68 + 0.70) / 2)
    assert fair_value(con, k, "yes", "2026-09-27T19:59:59+00:00") is None


def test_an_exchange_mid_in_play_is_never_a_fair_price(con):
    # The mid obeys the same start as the books: a quote captured at or
    # after the start is not a market opinion.
    k = _kalshi(con, yes_outcome=None)                      # mid .69 at PULL1
    for when in (START, INPLAY):
        store.insert_prices(con, kalshi.price_rows(k, LATE_BOOK, when,
                                                   "kalshi:quadratic:1", sport="nfl"))
    for at in (START, INPLAY):
        fv = fair_value(con, k, "yes", at)
        assert fv["as_of"] == store.canon_ts(PULL1)
        assert fv["p"] == pytest.approx((0.68 + 0.70) / 2)
    assert fair_value(con, k, "yes", INPLAY, max_age_s=600) is None


def test_a_game_contract_with_no_known_start_is_not_priced(con):
    # A contract on a game (its canonical id is a games row) with no start of
    # its own, on a game with no PRICED book market: the books' markets and
    # the games row say 00:20, but every book price was rejected, so
    # game_start has nothing. Its 01:00 in-play mid was the fair value 45
    # minutes after the start. Nothing can tell pregame from in play: refuse.
    sportsbook.write(con, "nfl", _event({"pinnacle": (-50, -50)}), PULL1)
    assert con.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 0
    k = {}
    for cev in ("nfl-g1", "weather:KNYC"):
        row = kalshi.market_row(f"KX-{cev}", canonical_event_id=cev, first_seen=PULL1,
                                sport="nfl", market_type="h2h", yes_outcome="home")
        store.upsert_markets(con, [row])
        k[cev] = row["market_id"]
        for when, book in ((PULL2, {"orderbook_fp": {"yes_dollars": [["0.6800", "10"]],
                                                     "no_dollars": [["0.3000", "10"]]}}),
                           ("2026-09-28T01:00:00+00:00", LATE_BOOK)):
            store.insert_prices(con, kalshi.price_rows(row["market_id"], book, when,
                                                       "kalshi:quadratic:1", sport="nfl"))
    assert game_start(con, store.market(con, k["nfl-g1"])) is None
    for at in (PULL2, "2026-09-28T01:05:00+00:00"):
        assert fair_value(con, k["nfl-g1"], "yes", at) is None
    # Not a game: no start is expected, and its own mid is its value.
    assert fair_value(con, k["weather:KNYC"], "yes", PULL2)["source"] == "kalshi-mid"


def test_stale_books_do_not_fall_through_to_an_in_play_mid(con):
    sportsbook.write(con, "nfl", _event({"pinnacle": (205, -230)}), PULL1)
    k = _kalshi(con, yes_outcome="home")
    store.insert_prices(con, kalshi.price_rows(k, LATE_BOOK, INPLAY,
                                               "kalshi:quadratic:1", sport="nfl"))
    assert fair_value(con, k, "yes", INPLAY)["source"] == "pinnacle"
    assert fair_value(con, k, "yes", INPLAY, max_age_s=600) is None


def test_the_kill_switch_takes_kalshi_sports_out_of_fair_value(con, monkeypatch):
    sportsbook.write(con, "nfl", _event({"pinnacle": (205, -230)}), PULL1)
    k = _kalshi(con)
    monkeypatch.setattr(config, "KALSHI_SPORTS_ENABLED", False)
    assert fair_value(con, k, "yes", PULL1) is None
    assert fair_value(con, _h2h("pinnacle"), "home", PULL1) is not None   # books unaffected
