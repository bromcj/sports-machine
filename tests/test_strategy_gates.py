"""The strategy registry and each strategy's own gates.

validation.json is a temp file in every test (validation.PATH is patched), and
the registry is emptied around each one."""
import datetime as dt
import itertools
import json
import random
import sqlite3
import threading
import time

import pytest

import db
from feeds import ET
from model import validation as v
from scanner import gates, paper, store, strategies
from scanner.strategies import Intent, Strategy
from scanner.venues import kalshi, sportsbook

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 3, 16, 0, tzinfo=UTC)
T1 = "T1. A test strategy's backtest"
T2 = "T2. Another test strategy's backtest"


@pytest.fixture
def env(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(v, "PATH", tmp_path / "validation.json")
    exp = tmp_path / "experiments.md"
    exp.write_text(f"# x\n\n### {T1}\n\n### {T2}\n\n```\n# T3. Inside a code fence\n```\n\n"
                   f"## Results log\n\n### T4. A result, not a pre-registration\n",
                   encoding="utf-8")
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


def test_gate_1_is_the_strategys_own_entry_and_a_real_pre_registration(env):
    _strategy()                                                  # on T1
    with pytest.raises(ValueError, match="pre-register"):       # a real, other entry
        gates.record_backtest("t_one", T2, True, "r", {"n": 1})
    with pytest.raises(ValueError, match="own entry"):          # one entry, two strategies
        _strategy("t_two")
    for name, entry in (("t_fence", "T3. Inside a code fence"),
                        ("t_log", "Results log"),
                        ("t_result", "T4. A result, not a pre-registration")):
        _strategy(name, experiment=entry)
        with pytest.raises(ValueError, match="pre-register"):
            gates.record_backtest(name, entry, True, "r", {"n": 1})
    assert all(v.status(n) is None for n in ("t_one", "t_fence", "t_log", "t_result"))


def test_a_retired_strategys_entry_is_still_its_own(env):
    # register() only sees the strategies loaded now. A retired strategy's
    # gate record still holds its entry, and a new strategy on it was
    # accepted (re-verification 2026-09-25, attack.py H).
    _strategy("t_old")
    gates.record_backtest("t_old", T1, True, "passed", {"n": 500})
    strategies.REGISTRY.pop("t_old")                  # its file deleted
    _strategy("t_new")                                # on the same entry
    before = v.PATH.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="own entry"):
        gates.record_backtest("t_new", T1, True, "passed", {"n": 1})
    assert v.PATH.read_text(encoding="utf-8") == before and v.status("t_new") is None


def test_a_backtest_can_never_be_written_onto_a_sports_block(env):
    # A sport's gate 1 is its walk-forward. The low-level recorder must not
    # turn one into a backtest PASS, whatever name it is handed.
    rows = [{"season": 2025, "logloss_model": .66, "logloss_market": .65,
             "n_games": 2000, "ll_diff_sd": 0.30}]
    v.record("mlb", v.REAL_MARKET, rows)                 # a real, failed gate 1
    v.record("_retired", v.REAL_MARKET, rows)            # a block not in SPORTS
    before = v.PATH.read_text(encoding="utf-8")
    for name in ("mlb", "nfl", "nba", "nhl", "_retired"):
        with pytest.raises(ValueError):
            v.record_backtest(name, T1, True, "typed", {"n": 1})
    assert v.PATH.read_text(encoding="utf-8") == before
    assert v.gates("mlb")["walk_forward"] is False
    v.record_backtest("t_one", T1, False, "lost", {"n": 1})   # a strategy's own
    assert v.record_backtest("t_one", T1, True, "won", {"n": 2})["cleared"] is True


def test_a_walk_forward_can_never_be_written_onto_a_strategys_block(env):
    # The mirror of the rule above: record() on a strategy's block turned a
    # failed backtest into gate 1 PASS (re-verification 2026-09-25).
    _strategy()
    gates.record_backtest("t_one", T1, False, "lost backtest", {"n": 500})
    before = v.PATH.read_text(encoding="utf-8")
    win = [{"season": 2025, "logloss_model": 0.60, "logloss_market": 0.68,
            "n_games": 2000, "ll_diff_sd": 0.1}]
    with pytest.raises(ValueError, match="strategy"):
        v.record("t_one", v.REAL_MARKET, win)
    assert v.PATH.read_text(encoding="utf-8") == before
    assert v.gates("t_one")["walk_forward"] is False
    assert v.record("_audit_probe", v.REAL_MARKET, win)["cleared"] is True   # not a strategy


# ------------------------------------------------------------------ gate 2 ---

ORDER_IDS = itertools.count(1)


def _positions(con, name, infos, mode="paper", settled=True, event=None):
    """Graded positions straight into the table: the gate reads rows. Each
    on a game of its own, unless `event` puts them all on one."""
    for i, x in enumerate(infos):
        oid = next(ORDER_IDS)
        start = (NOW - dt.timedelta(days=2, hours=i % 12)).isoformat()
        mid = f"m-{oid}"
        store.upsert_markets(con, [{"market_id": mid, "venue": "kalshi",
                                    "venue_market_id": mid,
                                    "canonical_event_id": event or f"e-{oid}",
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
    _strategy("t_two", experiment=T2)
    for name, entry in (("t_one", T1), ("t_two", T2)):
        gates.record_backtest(name, entry, True, "passed", {"n": 500})
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


def test_a_new_experiment_never_inherits_the_old_definitions_gate_2(env):
    # The name files the positions, so a new definition under the old name
    # would be re-passed on the old one's positions at its next look.
    _strategy()
    gates.record_backtest("t_one", T1, True, "passed", {"n": 500})
    rng = random.Random(1)
    _positions(env, "t_one", STRONG)
    _positions(env, "t_one", [rng.gauss(0, 2.9) for _ in range(60)], mode="placebo")
    assert gates.score(env, "t_one", NOW)["passed"]
    gates.record_backtest("t_one", T1, True, "re-run", {"n": 800})   # same test
    assert v.gates("t_one")["paper_trading"]
    before = v.PATH.read_text(encoding="utf-8")
    strategies.REGISTRY.pop("t_one")
    _strategy(experiment=T2)                         # the file edited: a new test
    with pytest.raises(ValueError, match="new name"):
        gates.record_backtest("t_one", T2, True, "passed", {"n": 800})
    assert v.PATH.read_text(encoding="utf-8") == before
    assert v.status("t_one")["experiment"] == T1


@pytest.mark.parametrize("edit", [dict(experiment=T2), dict(metric="realized_ev")])
def test_a_definition_edited_in_place_is_refused_by_gate_2_and_by_arm(env, edit):
    # The gate record is for one definition: its experiment and its metric.
    # With the file edited under the same name and gate 1 never re-recorded,
    # gate 2 re-passed on the old definition's positions and arm() opened
    # (re-verification 2026-09-25, attack.py A).
    _strategy()
    gates.record_backtest("t_one", T1, True, "passed", {"n": 500})
    rng = random.Random(1)
    _positions(env, "t_one", STRONG)
    _positions(env, "t_one", [rng.gauss(0, 2.9) for _ in range(60)], mode="placebo")
    assert gates.score(env, "t_one", NOW)["passed"]
    strategies.REGISTRY.pop("t_one")
    _strategy(**edit)                                 # the file, edited in place
    assert "new name" in v.arm("t_one") and not v.gates("t_one")["armed"]
    r = gates.score(env, "t_one", NOW + dt.timedelta(days=1))
    assert not r["passed"] and "new name" in r["reason"] and r["n_bets"] == 60
    assert v.arm("t_one").startswith("REFUSED")


def test_a_metric_flip_cannot_be_re_recorded_under_the_same_name(env):
    # realized_ev cannot pass gate 2; flipping the file to info and
    # re-recording gate 1 under the same name got round that, and arm()
    # opened (re-verification 2026-09-25, attack.py B).
    _strategy(metric="realized_ev")
    gates.record_backtest("t_one", T1, True, "passed", {"n": 500})
    assert v.status("t_one")["metric"] == "realized_ev"
    rng = random.Random(1)
    _positions(env, "t_one", STRONG)                  # rows hold info and realized_ev
    _positions(env, "t_one", [rng.gauss(0, 2.9) for _ in range(60)], mode="placebo")
    assert not gates.score(env, "t_one", NOW)["passed"]
    strategies.REGISTRY.pop("t_one")
    _strategy(metric="info")                          # the file, edited in place
    before = v.PATH.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="new name"):
        gates.record_backtest("t_one", T1, True, "passed", {"n": 500})
    assert v.PATH.read_text(encoding="utf-8") == before
    r = gates.score(env, "t_one", NOW + dt.timedelta(days=1))
    assert not r["passed"] and "new name" in r["reason"]
    assert v.arm("t_one").startswith("REFUSED")


def test_a_new_backtest_result_disarms_the_strategy(env):
    # Gate 3 is a person's decision about THIS result, not the next one.
    _strategy()
    gates.record_backtest("t_one", T1, True, "passed", {"n": 500})
    rng = random.Random(1)
    _positions(env, "t_one", STRONG)
    _positions(env, "t_one", [rng.gauss(0, 2.9) for _ in range(60)], mode="placebo")
    assert gates.score(env, "t_one", NOW)["passed"]
    assert "ARMED" in v.arm("t_one") and v.gates("t_one")["armed"]
    gates.record_backtest("t_one", T1, True, "re-run", {"n": 800})
    assert v.gates("t_one")["armed"] is False


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


def _looked_at_before(env):
    _strategy()
    gates.record_backtest("t_one", T1, False, "lost", {"n": 500})
    _positions(env, "t_one", STRONG[:10])


def test_the_look_cap_counts_the_et_day_not_the_utc_day(env):
    # 10:00 and 21:30 ET on Nov 3 are one ET day, though 21:30 ET is already
    # Nov 4 in UTC; 00:30 ET on Nov 4 starts the next one.
    _looked_at_before(env)
    et = lambda d, h, m=0: dt.datetime(2026, 11, d, h, m, tzinfo=ET).astimezone(UTC)
    assert gates.score(env, "t_one", et(3, 10)) is not None
    assert gates.score(env, "t_one", et(3, 21, 30)) is not None
    assert gates.score(env, "t_one", et(3, 23)) is None
    assert gates.score(env, "t_one", et(4, 0, 30)) is not None


def test_a_look_counts_even_if_the_caller_never_commits(env):
    _looked_at_before(env)
    for hours in (0, 1):
        assert gates.score(env, "t_one", NOW + dt.timedelta(hours=hours)) is not None
        env.rollback()
    assert gates.score(env, "t_one", NOW + dt.timedelta(hours=2)) is None
    assert env.execute("SELECT COUNT(*) FROM gate_looks").fetchone()[0] == 2


def test_a_busy_database_evaluates_nothing(env):
    _looked_at_before(env)
    env.commit()
    before = v.PATH.read_text(encoding="utf-8")
    other = db.connect()
    other.execute("BEGIN IMMEDIATE")                  # another writer holds the lock
    busy = sqlite3.connect(db.DB_PATH, timeout=0.1)
    busy.row_factory = sqlite3.Row
    try:
        with pytest.raises(sqlite3.OperationalError):
            gates.score(busy, "t_one", NOW)
    finally:
        other.rollback()
        other.close()
        busy.close()
    assert v.PATH.read_text(encoding="utf-8") == before
    assert env.execute("SELECT COUNT(*) FROM gate_looks").fetchone()[0] == 0


def test_two_scorers_at_once_still_get_two_looks_a_day(env, monkeypatch):
    _looked_at_before(env)
    assert gates.score(env, "t_one", NOW) is not None           # look 1
    env.commit()
    inside, measured = threading.Event(), gates.measured

    def slow(*a, **k):                  # the first scorer, mid-evaluation
        inside.set()
        time.sleep(0.5)
        return measured(*a, **k)

    monkeypatch.setattr(gates, "measured", slow)
    results = []

    def run():
        c = db.connect()
        try:
            results.append(gates.score(c, "t_one", NOW + dt.timedelta(hours=1)))
            c.commit()
        finally:
            c.close()

    first = threading.Thread(target=run)
    first.start()
    inside.wait(5)
    second = threading.Thread(target=run)
    second.start()
    first.join()
    second.join()
    assert env.execute("SELECT COUNT(*) FROM gate_looks").fetchone()[0] == 2
    assert sum(r is None for r in results) == 1


def test_the_strategies_command_says_so_when_the_tables_are_not_there(
        env, tmp_path, monkeypatch, capsys):
    # Production's database has no scanner tables until `python db.py`. A
    # free read must say so, not crash, and must not make them.
    _strategy()
    gates.record_backtest("t_one", T1, False, "lost", {"n": 500})
    before = v.PATH.read_text(encoding="utf-8")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "bare.db")
    assert gates.main([]) == 0 and gates.main(["--score"]) == 0
    out = capsys.readouterr().out
    assert "do not exist here yet" in out and "backtest FAIL (lost)" in out
    assert v.PATH.read_text(encoding="utf-8") == before
    bare = sqlite3.connect(tmp_path / "bare.db")
    assert bare.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0
    bare.close()


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
    # All 240 are due: only the 60 graded AND settled count.
    assert m["coverage"] == pytest.approx(60 / 240) and len(m["values"]) == 60
    assert len(m["placebo"]) == 60
    r = gates.score(env, "t_one", NOW)
    assert not r["passed"] and "COVERAGE" in r["reason"]
    assert v.arm("t_one").startswith("REFUSED")


def test_info_coverage_counts_positions_that_never_settled(env):
    # A settlement hook that settles a position only once it is graded never
    # settles an ungradable one. Divided by 'settled', those were invisible:
    # gate 2 passed, and arm() opened, at "100%" coverage with 60 of 260
    # resolved positions graded (re-verification 2026-09-25). The same 'due'
    # denominator realized_ev uses.
    _strategy()
    gates.record_backtest("t_one", T1, True, "passed", {"n": 500})
    rng = random.Random(1)
    _positions(env, "t_one", STRONG)                               # due, graded, settled
    _positions(env, "t_one", [None] * 200, settled=False)          # due, never settled
    _positions(env, "t_one", [rng.gauss(0, 2.9) for _ in range(60)], mode="placebo")
    assert gates.measured(env, "t_one", "info", NOW)["coverage"] == pytest.approx(60 / 260)
    r = gates.score(env, "t_one", NOW)
    assert not r["passed"] and "COVERAGE" in r["reason"]
    assert v.arm("t_one").startswith("REFUSED - gate 2")
    # Not due yet (resolved 2 hours ago): on neither side of the fraction.
    _positions(env, "t_one", [None] * 20, settled=False)
    _set_resolves_at(env, 20, (NOW - dt.timedelta(hours=2)).isoformat())
    assert gates.measured(env, "t_one", "info", NOW)["coverage"] == pytest.approx(60 / 260)


def test_gate_2_counts_events_not_positions(env):
    # Positions on one game share that game's move, so a second entry is not
    # a second piece of evidence. Counted per position, a no-skill strategy
    # entering each game k times passed 15% (k=2) to 72% (k=10), against 1.8%
    # at k=1 (Phase A re-verification, 2026-09-25). One value per event: the
    # mean of its positions.
    _strategy()
    gates.record_backtest("t_one", T1, True, "passed", {"n": 500})
    rng = random.Random(1)
    _positions(env, "t_one", [1.0, 2.0, 3.0] * 20, event="g1")     # 60 entries, one game
    _positions(env, "t_one", [5.0, 7.0], event="g2")
    _positions(env, "t_one", [rng.gauss(0, 2.9) for _ in range(60)], mode="placebo")
    m = gates.measured(env, "t_one", "info", NOW)
    assert m["values"] == [2.0, 6.0] and len(m["slots"]) == 2
    assert len(m["placebo"]) == 60 and m["coverage"] == 1.0      # coverage: positions
    r = gates.score(env, "t_one", NOW)
    assert not r["passed"] and r["n_bets"] == 2
    assert v.arm("t_one").startswith("REFUSED - gate 2")


def test_the_placebo_floor_is_50_events_not_50_rows(env):
    # 50 copies of one losing placebo game are one game of evidence.
    _strategy()
    gates.record_backtest("t_one", T1, True, "passed", {"n": 500})
    _positions(env, "t_one", STRONG)
    _positions(env, "t_one", [-0.5] * 60, mode="placebo", event="p1")
    r = gates.score(env, "t_one", NOW)
    assert not r["passed"] and "placebo has only 1 graded" in r["reason"]
    assert v.arm("t_one").startswith("REFUSED - gate 2")


def test_re_entering_one_game_through_the_pipeline_is_one_gate_2_value(env):
    # The natural shape of a 'price below fair' signal: it asks for the same
    # market every pass while the price stays attractive, and nothing in
    # run() or paper.submit refuses a second position on it.
    _game(env, {"pinnacle": (130, -150)}, NOW - dt.timedelta(minutes=5))
    row = kalshi.market_row("KXG", canonical_event_id="nba-g1", first_seen=NOW,
                            sport="nba", market_type="h2h", yes_outcome="home",
                            event_start=START, resolves_at="2026-11-04T03:00:00Z")
    store.upsert_markets(env, [row])
    book = {"orderbook_fp": {"yes_dollars": [["0.5500", "100"]],
                             "no_dollars": [["0.4000", "100"]]}}
    s = _strategy(signal=lambda con, now: [Intent(row["market_id"], "yes", "taker", 10, 0.65)],
                  placebo=lambda con, i, now: Intent(i.market_id, "no", "taker", 10, 0.65))
    for i in range(5):
        t = NOW + dt.timedelta(minutes=10 * i)
        store.insert_prices(env, kalshi.price_rows(row["market_id"], book, t,
                                                   "kalshi:quadratic:1", sport="nba"))
        strategies.run(env, s, t)
    _game(env, {"pinnacle": (140, -160)}, "2026-11-04T00:00:00Z")     # the close
    strategies.run(env, s, dt.datetime(2026, 11, 4, 0, 30, tzinfo=UTC))
    for (pid,) in env.execute("SELECT position_id FROM paper_positions").fetchall():
        paper.settle(env, pid, "win", "2026-11-04T04:00:00Z")
    graded = env.execute("SELECT COUNT(*) FROM paper_positions WHERE mode='paper'"
                         " AND info IS NOT NULL").fetchone()[0]
    m = gates.measured(env, "t_one", "info", NOW + dt.timedelta(days=3))
    assert graded >= 2 and len(m["values"]) == 1 and len(m["placebo"]) == 1


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
    s2 = _strategy("t_two", venues=("sportsbook",), signal=lambda con, now: [buy],
                   experiment=T2)
    out2 = strategies.run(env, s2, NOW)
    assert out2["orders"] == 0 and "not a venue" in out2["refused"][0]
