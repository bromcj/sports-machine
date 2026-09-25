"""Grading a paper position: `info` is the fair CLOSE against the fair price
at entry. Before the game starts there is no close yet - only the newest pull
so far - and a graded position is never graded again, so nothing is graded
until the market has resolved: by then the start, and so the close, is final."""
import datetime as dt

import pytest

import db
from scanner import paper, store, strategies
from scanner.fair import fair_value
from scanner.strategies import Strategy
from scanner.venues import kalshi, sportsbook

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 3, 16, 0, tzinfo=UTC)
START = "2026-11-04T00:10:00Z"                         # 7:10pm ET
BEFORE = dt.datetime(2026, 11, 3, 23, 31, tzinfo=UTC)  # 39 minutes before it


@pytest.fixture
def con(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(strategies, "REGISTRY", {})
    db.init()
    capsys.readouterr()
    c = db.connect()
    yield c
    c.close()


def _pinnacle(con, away, home, when):
    ev = [{"id": "g1", "commence_time": START, "home_team": "H", "away_team": "A",
           "bookmakers": [{"key": "pinnacle", "markets": [{"key": "h2h", "outcomes": [
               {"name": "A", "price": away}, {"name": "H", "price": home}]}]}]}]
    sportsbook.write(con, "nba", ev, when)


def _position(con, own_start=START):
    """A Kalshi 'home wins' YES position, filled a minute after NOW."""
    row = kalshi.market_row("KXG", canonical_event_id="nba-g1", first_seen=NOW,
                            sport="nba", market_type="h2h", yes_outcome="home",
                            event_start=own_start, resolves_at="2026-11-04T03:00:00Z")
    store.upsert_markets(con, [row])
    book = {"orderbook_fp": {"yes_dollars": [["0.5500", "100"]],
                             "no_dollars": [["0.4000", "100"]]}}
    for minute in (0, 1):
        store.insert_prices(con, kalshi.price_rows(
            row["market_id"], book, NOW + dt.timedelta(minutes=minute),
            "kalshi:quadratic:1", sport="nba"))
    paper.submit(con, strategy="t_one", mode="paper", market_id=row["market_id"],
                 outcome="yes", role="taker", size=10, limit_price=0.65, now=NOW)
    paper.simulate(con, NOW + dt.timedelta(minutes=1))
    return row["market_id"], con.execute(
        "SELECT position_id FROM paper_positions").fetchone()[0]


def _graded(con, pid):
    return con.execute("SELECT graded_at, fair_close_p, info FROM paper_positions"
                       " WHERE position_id=?", (pid,)).fetchone()


def test_a_pass_before_the_start_leaves_the_position_for_the_real_close(con):
    _pinnacle(con, 130, -150, NOW - dt.timedelta(minutes=5))
    mid, pid = _position(con)
    _pinnacle(con, 140, -160, "2026-11-03T23:30:00Z")   # inside the 60-minute window
    s = strategies.register(Strategy(
        name="t_one", venues=("kalshi",), signal=lambda c, n: [],
        placebo=lambda c, i, n: None, metric="info", experiment="T1"))
    r = paper.grade(con, pid, BEFORE)
    assert r["graded"] is False and "not started" in r["why"]
    assert strategies.run(con, s, BEFORE)["graded"] == 0
    assert _graded(con, pid)["graded_at"] is None       # still waiting for the close
    _pinnacle(con, 100, -120, "2026-11-04T00:05:00Z")   # the last pregame pull
    assert strategies.run(con, s, "2026-11-04T01:00:00Z")["graded"] == 0   # not resolved
    assert strategies.run(con, s, "2026-11-04T03:00:00Z")["graded"] == 1
    close = fair_value(con, mid, "yes", START)
    assert close["as_of"] == store.canon_ts("2026-11-04T00:05:00Z")
    g = _graded(con, pid)
    assert g["fair_close_p"] == close["p"] and g["info"] < 0   # home drifted out


def test_grading_reads_nothing_captured_after_the_moment_it_is_asked(con):
    # A replay: every pull is already stored. Asked at 23:31, the 00:05 close
    # is in the future and must not be used.
    _pinnacle(con, 130, -150, NOW - dt.timedelta(minutes=5))
    _, pid = _position(con)
    _pinnacle(con, 100, -120, "2026-11-04T00:05:00Z")
    assert paper.grade(con, pid, BEFORE)["graded"] is False
    assert paper.grade(con, pid, START)["graded"] is False     # started, not resolved
    # All the way to the resolution, not most of the way: a grade that
    # waited only an hour past the start passed every other test.
    assert paper.grade(con, pid, "2026-11-04T02:59:59Z")["graded"] is False
    assert paper.grade(con, pid, "2026-11-04T03:00:00Z")["graded"] is True


def test_a_delay_announced_after_the_start_does_not_freeze_an_early_close(con):
    # The game was due at 00:10. A pull at 00:12 says it will start at 01:10:
    # a delay, announced before 01:10, so the game's start moves (next_start).
    # A pass at 00:11 saw a start that had passed and graded against the
    # 00:08 pull - for good. Production's archive has 134 games whose start
    # moved later after the known start passed (re-verification, 2026-09-25).
    # Graded only once the market has resolved, the start is final.
    def pull(when, start, away, home):
        ev = [{"id": "g1", "commence_time": start, "home_team": "H", "away_team": "A",
               "bookmakers": [{"key": "pinnacle", "markets": [{"key": "h2h", "outcomes": [
                   {"name": "A", "price": away}, {"name": "H", "price": home}]}]}]}]
        sportsbook.write(con, "nba", ev, when)
    pull(NOW - dt.timedelta(minutes=5), START, 130, -150)
    mid, pid = _position(con, own_start=None)
    pull("2026-11-04T00:08:00Z", START, 140, -160)
    r = paper.grade(con, pid, "2026-11-04T00:11:00Z")
    assert r["graded"] is False and _graded(con, pid)["graded_at"] is None, r
    pull("2026-11-04T00:12:00Z", "2026-11-04T01:10:00Z", 140, -160)
    pull("2026-11-04T01:05:00Z", "2026-11-04T01:10:00Z", 100, -120)
    assert paper.grade(con, pid, "2026-11-04T03:00:00Z")["graded"] is True
    close = fair_value(con, mid, "yes", "2026-11-04T01:10:00Z")
    assert close["as_of"] == store.canon_ts("2026-11-04T01:05:00Z")
    assert _graded(con, pid)["fair_close_p"] == close["p"]


def test_grading_uses_the_games_start_not_the_contracts_own(con):
    # The contract says 01:10; the books say the game starts at 00:10.
    # fair_value cuts off at 00:10 (scanner.fair.game_start), so grade() does
    # too: the close is the last pull before 00:10, and the 60-minute window
    # is measured to 00:10. By the contract's own start it waited an hour
    # longer, then called the 00:05 close 65 minutes early.
    _pinnacle(con, 130, -150, NOW - dt.timedelta(minutes=5))
    mid, pid = _position(con, own_start="2026-11-04T01:10:00Z")
    _pinnacle(con, 100, -120, "2026-11-04T00:05:00Z")
    assert paper.grade(con, pid, "2026-11-04T03:00:00Z")["graded"] is True
    close = fair_value(con, mid, "yes", START)
    assert close["as_of"] == store.canon_ts("2026-11-04T00:05:00Z")
    assert _graded(con, pid)["fair_close_p"] == close["p"]
