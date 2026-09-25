"""The strategy registry and each strategy's own gates.

validation.json is a temp file in every test (validation.PATH is patched), and
the registry is emptied around each one."""
import datetime as dt
import itertools
import json
import random

import pytest

import db
from model import validation as v
from scanner import gates, paper, store, strategies
from scanner.strategies import Intent, Strategy
from scanner.venues import kalshi, sportsbook

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 3, 16, 0, tzinfo=UTC)
T1 = "T1. A test strategy's backtest"


@pytest.fixture
def env(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(v, "PATH", tmp_path / "validation.json")
    exp = tmp_path / "experiments.md"
    exp.write_text("# x\n\n### T1. A test strategy's backtest\n", encoding="utf-8")
    monkeypatch.setattr(gates, "EXPERIMENTS", exp)
    monkeypatch.setattr(strategies, "REGISTRY", {})
    db.init()
    capsys.readouterr()
    c = db.connect()
    yield c
    c.close()


def _strategy(name="t_one", metric="info", **kw):
    args = dict(name=name, venues=("kalshi",), signal=lambda con, now: [],
                placebo=lambda con, intent, now: None, metric=metric,
                experiment=T1)
    args.update(kw)
    return strategies.register(Strategy(**args))


# --------------------------------------------------------------- registry ---

def test_a_strategy_must_be_well_formed_to_register(env):
    for bad in (dict(name="MLB"), dict(name="mlb"), dict(name="x"),
                dict(metric="clv"), dict(placebo=None), dict(experiment=""),
                dict(venues=()), dict(venues=("predictit",))):
        with pytest.raises(ValueError):
            _strategy(**bad)
    _strategy()
    with pytest.raises(ValueError, match="already"):
        _strategy()                                  # a second, different object


def test_adding_a_strategy_is_a_file(env, tmp_path, monkeypatch):
    folder = tmp_path / "more_strategies"
    folder.mkdir()
    (folder / "newone.py").write_text(
        "from scanner.strategies import Strategy, register\n"
        "register(Strategy(name='newone', venues=('kalshi',),\n"
        "    signal=lambda c, n: [], placebo=lambda c, i, n: None,\n"
        "    metric='realized_ev', experiment='T1'))\n", encoding="utf-8")
    import sys
    monkeypatch.setattr(strategies, "__path__", [str(folder)])
    try:
        assert "newone" in strategies.load()           # found by existing code
    finally:
        sys.modules.pop("scanner.strategies.newone", None)


def test_phase_a_ships_no_strategies():
    import pkgutil
    assert [m.name for m in pkgutil.iter_modules(strategies.__path__)] == []


# ------------------------------------------------------------------ gate 1 ---

def test_gate_1_needs_a_registered_strategy_and_a_preregistered_experiment(env):
    with pytest.raises(KeyError):
        gates.record_backtest("t_one", T1, True, "r", {"n": 1})
    _strategy()
    for loose in ("T9", "T1", "T1."):            # absent, or only a prefix
        with pytest.raises(ValueError, match="pre-register"):
            gates.record_backtest("t_one", loose, True, "r", {"n": 1})
    e = gates.record_backtest("t_one", T1, False, "lost", {"n": 100})
    assert e["kind"] == "strategy" and e["cleared"] is False and e["armed"] is False
    assert "backtest FAIL (lost)" in v.explain("t_one")


# ------------------------------------------------------------------ gate 2 ---

ORDER_IDS = itertools.count(1)


def _positions(con, name, infos, mode="paper", settled=True):
    """Graded positions straight into the table: the gate reads rows."""
    for i, x in enumerate(infos):
        oid = next(ORDER_IDS)
        start = (NOW - dt.timedelta(days=2, hours=i % 12)).isoformat()
        mid = f"m-{oid}"
        store.upsert_markets(con, [{"market_id": mid, "venue": "kalshi",
                                    "venue_market_id": mid, "canonical_event_id": "e",
                                    "market_type": "binary", "event_start": start,
                                    "resolves_at": start, "first_seen": start}])
        con.execute("INSERT INTO paper_positions (order_id, strategy, mode, venue,"
                    " market_id, outcome, contracts, avg_price, fee, stake, opened_at,"
                    " resolves_at, info, result, realized_ev) VALUES"
                    " (?,?,?,'kalshi',?,'yes',10,0.5,0.1,5.1,?,?,?,?,?)",
                    (oid, name, mode, mid, start, start, x,
                     "win" if settled else None, x if settled else None))


STRONG = [1.4 + (i % 7 - 3) * 0.4 for i in range(60)]


def test_score_writes_nothing_without_a_gate_1_record(env):
    _strategy()
    _positions(env, "t_one", STRONG)
    assert gates.score(env, "t_one", NOW) is None
    assert v.status("t_one") is None
    assert env.execute("SELECT COUNT(*) FROM gate_looks").fetchone()[0] == 0


def test_a_strategy_passes_gate_2_on_its_own_positions_only(env):
    _strategy("t_one")
    _strategy("t_two")
    for name in ("t_one", "t_two"):
        gates.record_backtest(name, T1, True, "passed", {"n": 500})
    rng = random.Random(1)
    _positions(env, "t_one", STRONG)
    _positions(env, "t_one", [rng.gauss(0, 2.9) for _ in range(60)], mode="placebo")
    r = gates.score(env, "t_one", NOW)
    assert r["passed"], r["reason"]
    # t_two has none of t_one's positions and inherits nothing.
    r2 = gates.score(env, "t_two", NOW)
    assert not r2["passed"] and r2["n_bets"] == 0
    assert "ARMED" in v.arm("t_one")
    assert v.arm("t_two").startswith("REFUSED")


def test_a_placebo_that_also_passes_blocks_the_strategy(env):
    _strategy()
    gates.record_backtest("t_one", T1, True, "passed", {"n": 500})
    _positions(env, "t_one", STRONG)
    _positions(env, "t_one", STRONG, mode="placebo")
    r = gates.score(env, "t_one", NOW)
    assert not r["passed"] and "PLACEBO" in r["reason"].upper()
    assert v.arm("t_one").startswith("REFUSED")


def test_a_placebo_with_too_little_evidence_blocks_the_strategy(env):
    # A placebo that placed nothing, or whose positions were never graded,
    # cannot show the gate is measuring the strategy and not a drift.
    _strategy()
    gates.record_backtest("t_one", T1, True, "passed", {"n": 500})
    _positions(env, "t_one", STRONG)
    r = gates.score(env, "t_one", NOW)
    assert not r["passed"] and "placebo has only 0" in r["reason"]
    _positions(env, "t_one", STRONG[:49], mode="placebo")          # as good as it
    _positions(env, "t_one", [None] * 30, mode="placebo")          # never graded
    r = gates.score(env, "t_one", NOW + dt.timedelta(hours=1))
    assert not r["passed"] and "placebo has only 49" in r["reason"]
    assert v.arm("t_one").startswith("REFUSED - gate 2")


def test_arm_refuses_while_gate_1_fails_however_good_gate_2_is(env):
    _strategy()
    gates.record_backtest("t_one", T1, False, "lost", {"n": 500})
    rng = random.Random(1)
    _positions(env, "t_one", STRONG)
    _positions(env, "t_one", [rng.gauss(0, 2.9) for _ in range(60)], mode="placebo")
    assert gates.score(env, "t_one", NOW)["passed"]
    assert v.arm("t_one").startswith("REFUSED - gate 1")


def test_what_score_writes_is_what_the_scheduled_job_may_discard(env):
    _strategy()
    gates.record_backtest("t_one", T1, False, "lost", {"n": 500})
    before = json.loads(v.PATH.read_text(encoding="utf-8"))
    _positions(env, "t_one", STRONG[:30])
    gates.score(env, "t_one", NOW)
    after = json.loads(v.PATH.read_text(encoding="utf-8"))
    assert before != after
    assert v.only_paper_changed(before, after)


def test_gate_2_is_looked_at_no_more_than_twice_an_et_day(env):
    _strategy()
    gates.record_backtest("t_one", T1, False, "lost", {"n": 500})
    _positions(env, "t_one", STRONG[:10])
    assert gates.score(env, "t_one", NOW) is not None
    assert gates.score(env, "t_one", NOW + dt.timedelta(hours=1)) is not None
    assert gates.score(env, "t_one", NOW + dt.timedelta(hours=2)) is None
    assert env.execute("SELECT COUNT(*) FROM gate_looks").fetchone()[0] == 2
    assert gates.score(env, "t_one", NOW + dt.timedelta(days=1)) is not None


def test_realized_ev_coverage_counts_positions_that_should_have_settled(env):
    _strategy(metric="realized_ev")
    gates.record_backtest("t_one", T1, False, "lost", {"n": 500})
    _positions(env, "t_one", STRONG[:40])
    _positions(env, "t_one", [0.0] * 40, settled=False)        # never settled
    m = gates.measured(env, "t_one", "realized_ev", NOW)
    assert len(m["values"]) == 40 and m["coverage"] == pytest.approx(0.5)


def _set_resolves_at(con, last_n, when):
    con.execute("UPDATE paper_positions SET resolves_at=? WHERE position_id IN"
                " (SELECT position_id FROM paper_positions ORDER BY position_id DESC"
                " LIMIT ?)", (when, last_n))


def test_realized_ev_coverage_is_not_topped_up_by_recent_settlements(env):
    # Top and bottom of the fraction are the same positions: those due.
    _strategy(metric="realized_ev")
    _positions(env, "t_one", STRONG[:40])                       # due, settled
    _positions(env, "t_one", [0.0] * 40, settled=False)         # due, never settled
    _positions(env, "t_one", STRONG[:20])                       # settled, not yet due
    _set_resolves_at(env, 20, (NOW - dt.timedelta(hours=2)).isoformat())
    _positions(env, "t_one", STRONG[:10])                       # no resolves_at: count
    _set_resolves_at(env, 10, None)                             # as due (fail closed)
    _positions(env, "t_one", [0.0] * 10, settled=False)
    _set_resolves_at(env, 10, None)
    m = gates.measured(env, "t_one", "realized_ev", NOW)
    assert m["coverage"] == pytest.approx(50 / 100)


def test_a_realized_ev_strategy_cannot_pass_gate_2_until_its_bar_is_calibrated(env):
    # A no-skill favourite buyer wins almost every time, so its realized_ev
    # clears 3 SE easily (review 2026-09-25: 5-41% false passes). Refused,
    # the evidence still recorded, and only in the part the job may discard.
    _strategy(metric="realized_ev")
    gates.record_backtest("t_one", T1, True, "passed", {"n": 500})
    before = json.loads(v.PATH.read_text(encoding="utf-8"))
    rng = random.Random(1)
    _positions(env, "t_one", [(1.8, 2.8, 3.8)[i % 3] for i in range(60)])  # 60 wins
    _positions(env, "t_one", [rng.gauss(0, 2.9) for _ in range(60)], mode="placebo")
    r = gates.score(env, "t_one", NOW)
    assert not r["passed"] and "realized_ev" in r["reason"] and "calibrat" in r["reason"]
    assert r["n_bets"] == 60 and r["t_stat"] > 3
    assert not v.gates("t_one")["paper_trading"] and v.arm("t_one").startswith("REFUSED")
    assert v.only_paper_changed(before, json.loads(v.PATH.read_text(encoding="utf-8")))


def test_info_coverage_counts_only_graded_positions_that_settled(env):
    # The scanner grades at the start and settles later, so a graded position
    # may not have settled. It must not stand in for a settled, ungraded one.
    _strategy()
    gates.record_backtest("t_one", T1, True, "passed", {"n": 500})
    rng = random.Random(1)
    _positions(env, "t_one", STRONG)                                # graded, settled
    _positions(env, "t_one", [None] * 90)                           # settled, ungraded
    _positions(env, "t_one", STRONG + STRONG[:30], settled=False)   # graded, unsettled
    _positions(env, "t_one", [rng.gauss(0, 2.9) for _ in range(60)], mode="placebo")
    _positions(env, "t_one", [5.0] * 60, mode="placebo", settled=False)
    m = gates.measured(env, "t_one", "info", NOW)
    assert m["coverage"] == pytest.approx(60 / 150) and len(m["values"]) == 60
    assert len(m["placebo"]) == 60
    r = gates.score(env, "t_one", NOW)
    assert not r["passed"] and "COVERAGE" in r["reason"]
    assert v.arm("t_one").startswith("REFUSED")


# ---------------------------------------------------------------- grading ---

START = "2026-11-04T00:10:00Z"


def _game(con, books, when):
    ev = [{"id": "g1", "commence_time": START, "home_team": "H", "away_team": "A",
           "bookmakers": [{"key": k, "markets": [{"key": "h2h", "outcomes": [
               {"name": "A", "price": a}, {"name": "H", "price": h}]}]}
               for k, (a, h) in books.items()]}]
    sportsbook.write(con, "nba", ev, when)


def _kalshi_position(con):
    row = kalshi.market_row("KXG", canonical_event_id="nba-g1", first_seen=NOW,
                            sport="nba", market_type="h2h", yes_outcome="home",
                            event_start=START, resolves_at="2026-11-04T03:00:00Z")
    store.upsert_markets(con, [row])
    store.insert_prices(con, kalshi.price_rows(
        row["market_id"], {"orderbook_fp": {"yes_dollars": [["0.5500", "100"]],
                                            "no_dollars": [["0.4000", "100"]]}},
        NOW, "kalshi:quadratic:1", sport="nba"))
    oid = paper.submit(con, strategy="t_one", mode="paper", market_id=row["market_id"],
                       outcome="yes", role="taker", size=10, limit_price=0.65, now=NOW)
    store.insert_prices(con, kalshi.price_rows(
        row["market_id"], {"orderbook_fp": {"yes_dollars": [["0.5500", "100"]],
                                            "no_dollars": [["0.4000", "100"]]}},
        NOW + dt.timedelta(minutes=1), "kalshi:quadratic:1", sport="nba"))
    paper.simulate(con, NOW + dt.timedelta(minutes=1))
    return con.execute("SELECT position_id FROM paper_positions WHERE order_id=?",
                       (oid,)).fetchone()[0]


def test_info_is_graded_when_both_ends_are_pinnacle(env):
    _game(env, {"pinnacle": (130, -150)}, NOW - dt.timedelta(minutes=5))
    pid = _kalshi_position(env)
    _game(env, {"pinnacle": (140, -160)}, "2026-11-04T00:00:00Z")   # 10 min out
    r = paper.grade(env, pid, NOW + dt.timedelta(hours=9))
    assert r["graded"] and r["info"] > 0                # home got shorter: moved our way


def test_a_switch_of_fair_source_leaves_a_position_ungraded(env):
    _game(env, {"draftkings": (130, -150)}, NOW - dt.timedelta(minutes=5))
    pid = _kalshi_position(env)
    _game(env, {"pinnacle": (140, -160)}, "2026-11-04T00:00:00Z")
    r = paper.grade(env, pid, NOW + dt.timedelta(hours=9))
    assert not r["graded"] and "consensus -> pinnacle" in r["why"]


def test_a_close_taken_too_early_leaves_a_position_ungraded(env):
    _game(env, {"pinnacle": (130, -150)}, NOW - dt.timedelta(minutes=5))
    pid = _kalshi_position(env)                         # last book: 8+ hours out
    r = paper.grade(env, pid, NOW + dt.timedelta(hours=9))
    assert not r["graded"] and "window" in r["why"]


def test_one_pass_of_a_strategy_places_its_order_and_its_placebo(env):
    _game(env, {"pinnacle": (130, -150)}, NOW - dt.timedelta(minutes=5))
    row = kalshi.market_row("KXG", canonical_event_id="nba-g1", first_seen=NOW,
                            sport="nba", market_type="h2h", yes_outcome="home",
                            event_start=START)
    store.upsert_markets(env, [row])
    store.insert_prices(env, kalshi.price_rows(
        row["market_id"], {"orderbook_fp": {"yes_dollars": [["0.5500", "100"]],
                                            "no_dollars": [["0.4000", "100"]]}},
        NOW, "kalshi:quadratic:1", sport="nba"))
    buy = Intent(row["market_id"], "yes", "taker", 10, 0.62)
    s = _strategy(signal=lambda con, now: [buy],
                  placebo=lambda con, i, now: Intent(i.market_id, "no", "taker", 10, 0.62))
    out = strategies.run(env, s, NOW)
    assert (out["intents"], out["orders"], out["placebos"]) == (1, 1, 1)
    modes = [r[0] for r in env.execute("SELECT mode FROM paper_orders ORDER BY order_id")]
    assert modes == ["paper", "placebo"]
    # A venue the strategy does not trade is refused before any order.
    s2 = _strategy("t_two", venues=("sportsbook",), signal=lambda con, now: [buy])
    out2 = strategies.run(env, s2, NOW)
    assert out2["orders"] == 0 and "not a venue" in out2["refused"][0]
