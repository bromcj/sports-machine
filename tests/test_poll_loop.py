"""The polling loop, driven by a fake clock and a fake API. No network: every
HTTP call goes to FakeGet, which also proves no call happens before the budget
has been checked."""
import datetime as dt
import json
import sqlite3
import subprocess
import sys
import threading
import time

import pytest
import requests

import config
import db
from ingest import http
from scanner import budget, poll

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 11, 3, 15, 0, tzinfo=UTC)       # 10:00 ET


class Resp:
    def __init__(self, payload, cost, remaining=18000, status=200):
        self.text = json.dumps(payload)
        self._payload = payload
        self.status_code = status
        self.headers = {"x-requests-last": str(cost),
                        "x-requests-remaining": str(remaining),
                        "x-requests-used": str(20000 - remaining)}

    def json(self):
        return self._payload


def _events(starts):
    return [{"id": f"e{i}", "commence_time": s.isoformat().replace("+00:00", "Z"),
             "home_team": f"Home {i}", "away_team": f"Away {i}",
             "bookmakers": [{"key": "pinnacle", "markets": [
                 {"key": "h2h", "outcomes": [{"name": f"Away {i}", "price": 120},
                                             {"name": f"Home {i}", "price": -140}]}]}]}
            for i, s in enumerate(starts)]


class FakeGet:
    """Stands in for ingest.http.get. Records every call."""

    def __init__(self, starts, con_factory=None, fail=None):
        self.starts, self.calls, self.fail = starts, [], fail
        self.con_factory = con_factory

    def __call__(self, url, params=None, label="", attempts=3, **_):
        kind = "odds" if url.endswith("/odds") else "events"
        self.calls.append((kind, attempts))
        if self.fail and kind == "odds":
            r = Resp({"message": "no"}, 0, status=self.fail)
            raise requests.HTTPError(f"{self.fail} Client Error", response=r)
        return Resp(_events(self.starts), 0 if kind == "events" else budget.call_cost())


@pytest.fixture
def env(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(poll, "STATE_DIR", tmp_path / "scanner")
    monkeypatch.setattr(poll, "HEARTBEAT", tmp_path / "scanner" / "poll.json")
    monkeypatch.setattr(poll, "STOP", tmp_path / "scanner" / "poll.stop")
    monkeypatch.setattr(poll, "LOCK", tmp_path / "scanner" / "poll.lock")
    monkeypatch.setattr(poll, "RECORD", tmp_path / "scanner" / "ledger-of-record")
    monkeypatch.setattr(config, "CREDIT_CAP_BRIEF", 6000)
    monkeypatch.setattr(config, "BRIEF_POLL_DAYS", 42)
    db.init()
    capsys.readouterr()
    return tmp_path


def _poller(get, clock, sports=("nba",)):
    src = poll.OddsSource(get=get, key="test-key", clock=clock)
    return poll.Poller(src, sports=list(sports), clock=clock, sleep=lambda s: None,
                       out=lambda *a: None)


def test_a_due_sport_is_polled_and_its_prices_stored(env):
    start = T0 + dt.timedelta(minutes=45)                # closing tier
    get = FakeGet([start])
    p = _poller(get, lambda: T0)
    assert p.tick(T0) == ["nba"]
    con = db.connect()
    assert con.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 2
    ledger = con.execute("SELECT endpoint, estimated, cost, remaining FROM credit_ledger"
                         " ORDER BY id").fetchall()
    assert [tuple(r) for r in ledger] == [("events", 0, 0, 18000),
                                          ("odds", 3, 3, 18000)]
    # The account balance reaches api_usage, where monitor.py reads it.
    assert con.execute("SELECT remaining FROM api_usage").fetchall()[-1][0] == 18000
    con.close()


def test_metered_calls_are_never_retried_automatically(env):
    get = FakeGet([T0 + dt.timedelta(minutes=45)])
    _poller(get, lambda: T0).tick(T0)
    assert ("odds", 1) in get.calls and ("events", 3) in get.calls


def test_it_waits_for_the_interval_and_stops_when_the_games_start(env):
    start = T0 + dt.timedelta(minutes=45)
    get = FakeGet([start])
    p = _poller(get, lambda: T0)
    p.tick(T0)
    p.level_at = T0                        # hold the level fixed for this test
    assert p.tick(T0 + dt.timedelta(seconds=30)) == []
    lv_interval = budget.LADDER[p.level][0]
    assert p.tick(T0 + dt.timedelta(minutes=lv_interval)) == ["nba"]
    assert p.tick(start) == []             # started: nothing left to price
    assert p.tick(start + dt.timedelta(hours=1)) == []


def test_no_request_is_made_when_the_brief_cap_would_be_passed(env, monkeypatch):
    monkeypatch.setattr(config, "CREDIT_CAP_BRIEF", 2)
    get = FakeGet([T0 + dt.timedelta(minutes=45)])
    p = _poller(get, lambda: T0)
    p.level = 6
    p.level_at = T0
    p.schedule_at = T0
    p.schedule = {"nba": [T0 + dt.timedelta(minutes=45)]}
    with pytest.raises(poll.Fatal) as e:
        p.tick(T0)
    assert e.value.kind == "budget"
    assert not [c for c in get.calls if c[0] == "odds"]      # nothing was requested


def test_the_daily_pace_makes_the_loop_wait_not_stop(env, monkeypatch):
    monkeypatch.setattr(config, "CREDIT_CAP_BRIEF", 84)       # 84 / 42 = 2 a day
    get = FakeGet([T0 + dt.timedelta(minutes=45)])
    p = _poller(get, lambda: T0)
    p.level, p.level_at, p.schedule_at = 6, T0, T0
    p.schedule = {"nba": [T0 + dt.timedelta(minutes=45)]}
    assert p.tick(T0) == []                                   # a 3-credit call > 2
    assert p.state.startswith("waiting") and p.level is None
    assert not [c for c in get.calls if c[0] == "odds"]


def test_each_limit_says_which_it_is(env, monkeypatch):
    con = db.connect()
    monkeypatch.setattr(config, "CREDIT_CAP_BRIEF", 2)
    with pytest.raises(budget.OverBudget) as e:
        budget.check(con, 3, T0)
    assert e.value.limit == "brief"
    monkeypatch.setattr(config, "BRIEF_ACTIVE", False)
    monkeypatch.setattr(config, "POLL_MONTHLY_BUDGET", 2)
    with pytest.raises(budget.OverBudget) as e:
        budget.check(con, 3, T0)
    assert e.value.limit == "month"
    con.close()


def test_a_refused_key_stops_the_loop_and_is_ledgered(env):
    get = FakeGet([T0 + dt.timedelta(minutes=45)], fail=401)
    p = _poller(get, lambda: T0)
    assert p.run(max_ticks=3) == 1
    hb = poll.heartbeat()
    assert hb["state"].startswith("stopped:") and hb["stop_kind"] == "api"
    con = db.connect()
    row = con.execute("SELECT ok, estimated, cost FROM credit_ledger WHERE endpoint='odds'").fetchone()
    con.close()
    assert tuple(row) == (0, 3, 0)


def test_a_restart_remembers_the_last_poll_from_the_ledger(env):
    start = T0 + dt.timedelta(minutes=45)
    get = FakeGet([start])
    _poller(get, lambda: T0).tick(T0)
    again = _poller(get, lambda: T0)
    assert again.tick(T0 + dt.timedelta(seconds=40)) == []     # not a second poll


# ------------------------------------------- no ledger row, no request ---

def _impatient_connect():
    """db.connect with a 0.1 s busy wait instead of 5 s, so a held lock fails
    fast."""
    c = db.connect()
    c.execute("PRAGMA busy_timeout = 100")
    return c


def test_no_request_is_made_while_the_ledger_cannot_be_written(env):
    # Another process mid-write holds the database. A request made now could
    # be billed and never recorded, and no limit would see it.
    blocker = sqlite3.connect(db.DB_PATH, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        get = FakeGet([T0 + dt.timedelta(minutes=45)])
        p = _poller(get, lambda: T0)
        p.connect = _impatient_connect
        p.level, p.level_at, p.schedule_at = 6, T0, T0
        p.schedule = {"nba": [T0 + dt.timedelta(minutes=45)]}
        assert p.tick(T0) == []                              # the odds call
        p.schedule_at = None
        p.tick(T0 + dt.timedelta(minutes=1))                 # the free events list
        assert get.calls == []
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()


@pytest.mark.parametrize("lock", ["BEGIN IMMEDIATE", "BEGIN; SELECT COUNT(*) FROM prices"])
def test_a_billed_call_counts_even_when_its_result_cannot_be_written(env, lock):
    # The request is made and billed; then another connection takes a write
    # lock, or holds a read open across the commit. The call must still count.
    blockers = []

    class LockAfter(FakeGet):
        def __call__(self, url, **kw):
            r = super().__call__(url, **kw)
            b = sqlite3.connect(db.DB_PATH, isolation_level=None)
            for stmt in lock.split("; "):
                b.execute(stmt).fetchall()
            blockers.append(b)
            return r

    get = LockAfter([T0 + dt.timedelta(minutes=45)])
    con = _impatient_connect()
    with pytest.raises(sqlite3.OperationalError):
        poll.OddsSource(get=get, key="test-key", clock=lambda: T0).poll(con, "nba", T0)
    con.close()
    for b in blockers:
        b.execute("ROLLBACK")
        b.close()
    assert get.calls == [("odds", 1)]                        # billed once
    con = db.connect()
    assert budget.spent(con) == budget.call_cost()           # and counted once
    con.close()


def test_two_loops_cannot_both_spend_the_last_credits(env):
    # 5,997 of 6,000 spent over 41 earlier ET days: room for one 3-credit call.
    con = db.connect()
    for i in range(41):
        budget.record(con, consumer="poll", endpoint="odds", sport="nba", estimated=3,
                      cost=146 if i < 40 else 5997 - 146 * 40, ok=True,
                      ts=T0 - dt.timedelta(days=41 - i))
    con.commit()
    con.close()
    in_get, release, calls, results = threading.Event(), threading.Event(), [], {}

    def get(url, params=None, label="", attempts=3, **_):
        calls.append(label)
        in_get.set()
        if len(calls) > 1:
            release.set()
        release.wait(10)                    # A's request is in flight until B is done
        return Resp([], budget.call_cost())

    def loop(name):
        c = db.connect()
        try:
            poll.OddsSource(get=get, key="test-key", clock=lambda: T0).poll(c, "nba", T0)
            results[name] = "polled"
        except budget.OverBudget as e:
            results[name] = e.limit
        finally:
            c.close()
            release.set()

    a = threading.Thread(target=loop, args=("A",))
    a.start()
    assert in_get.wait(10)
    b = threading.Thread(target=loop, args=("B",))
    b.start()
    a.join(20)
    b.join(20)
    con = db.connect()
    brief = budget.status(con, T0)["brief_spent"]
    con.close()
    assert (len(calls), sorted(results.values()), brief) == (1, ["brief", "polled"], 6000)


def test_a_stop_file_ends_the_loop(env):
    get = FakeGet([T0 + dt.timedelta(days=3)])
    p = _poller(get, lambda: T0)
    ticks = []
    p.sleep = lambda s: (ticks.append(s), poll.STOP.write_text("stop"))
    assert p.run(max_ticks=10) == 0
    assert len(ticks) == 1 and poll.heartbeat()["state"] == "stopped: asked to"


def test_the_heartbeat_names_the_code_the_loop_started_on(env, monkeypatch):
    # After a `git pull` the checkout's HEAD moves, but a running process
    # keeps the code it imported. Only the commit it started on tells
    # ensure() that it needs a restart.
    monkeypatch.setattr(db, "code_sha", lambda: "aaaaaaa")
    p = _poller(FakeGet([T0 + dt.timedelta(days=3)]), lambda: T0)
    monkeypatch.setattr(db, "code_sha", lambda: "bbbbbbb")     # the pull
    p.run(max_ticks=1)
    assert poll.heartbeat()["code_sha"] == "aaaaaaa"


def test_the_heartbeat_is_stamped_when_it_is_written(env, monkeypatch):
    # A server that hangs makes the hourly tick last 378 s: 3 events lists x
    # (3 x 30 s + 2 + 4 s backoff) + 3 odds calls x 30 s. A beat stamped with
    # the tick's start is already 378 s old when written - past ALIVE_WITHIN.
    T = {"now": dt.datetime(2026, 11, 3, 23, 0, tzinfo=UTC)}

    def hanging_get(url, params=None, timeout=30):
        T["now"] += dt.timedelta(seconds=timeout)
        raise requests.Timeout("read timed out")

    monkeypatch.setattr(http.requests, "get", hanging_get)
    monkeypatch.setattr(http.time, "sleep",
                        lambda s: T.__setitem__("now", T["now"] + dt.timedelta(seconds=s)))
    clock = lambda: T["now"]                                   # noqa: E731
    p = poll.Poller(poll.OddsSource(get=http.get, key="test-key", clock=clock),
                    sports=["nfl", "nba", "nhl"], clock=clock, sleep=lambda s: None,
                    out=lambda *a: None)
    p.schedule = {sp: [T["now"] + dt.timedelta(minutes=60)] for sp in p.sports}
    p.schedule_at = T["now"] - poll.SCHEDULE_EVERY            # the hourly refresh is due
    p.level, p.level_at = 0, T["now"]
    p.last_poll = {sp: T["now"] - dt.timedelta(hours=1) for sp in p.sports}
    start = T["now"]
    p.run(max_ticks=1)
    assert (T["now"] - start).total_seconds() == 378
    assert poll.alive(poll.heartbeat(), T["now"])


# Stands in for a running `poll --live`: the OS lock on data/scanner/poll.lock,
# held until stdin closes.
HOLD = """
import sys
f = open(sys.argv[1], "a+b")
f.seek(0)
if sys.platform == "win32":
    import msvcrt
    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
else:
    import fcntl
    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
print("held", flush=True)
sys.stdin.read()
"""


def test_a_loop_that_holds_the_lock_is_running_however_old_its_beat(env, monkeypatch):
    # A slow tick, a sleeping PC, or a first tick not finished yet: the
    # heartbeat can be minutes old, or missing, while the loop is there.
    monkeypatch.setattr(config, "POLLING_ENABLED", True)
    for name in ("Poller", "OddsSource"):
        monkeypatch.setattr(poll, name, lambda *a, **k: pytest.fail("a second loop"))

    class P:
        pid = 4242
    started = []
    spawn = lambda: (started.append(1), P())[1]               # noqa: E731
    lock = env / "scanner" / "poll.lock"
    lock.parent.mkdir(exist_ok=True)
    poll.RECORD.write_text("")                     # so only the lock can refuse
    poll.HEARTBEAT.write_text(json.dumps({
        "state": "running", "pid": 7, "code_sha": db.code_sha(),
        "beat_at": (T0 - dt.timedelta(hours=8)).isoformat()}))
    holder = subprocess.Popen([sys.executable, "-c", HOLD, str(lock)], text=True,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        assert holder.stdout.readline().strip() == "held"
        assert poll.ensure(spawn=spawn, now=T0).startswith("running")
        assert poll.live() == 2
        assert not started
    finally:
        holder.stdin.close()
        holder.wait(10)
    # The OS let go when the holder ended: no stale lock.
    deadline = time.monotonic() + 10
    while poll.locked() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert poll.ensure(spawn=spawn, now=T0).startswith("started") and started == [1]
    mine = poll.take_lock()
    assert mine is not None and poll.take_lock() is None       # one loop, even here
    mine.close()


# ------------------------------------------------------------ supervisor ---

def test_ensure_does_nothing_while_polling_is_switched_off(env, monkeypatch):
    monkeypatch.setattr(config, "POLLING_ENABLED", False)
    started = []
    msg = poll.ensure(spawn=lambda: started.append(1))
    assert not started and "switched off" in msg
    assert not poll.STOP.exists()                             # no loop, nothing to stop
    assert poll.live() == 2                                   # --live refuses too


def test_switching_polling_off_stops_a_running_loop(env, monkeypatch):
    # A loop started while polling was on keeps its own copy of config. The
    # scheduled job's --ensure, after the pull that brings the False, is
    # what reaches it.
    monkeypatch.setattr(config, "POLLING_ENABLED", False)
    get = FakeGet([T0 + dt.timedelta(minutes=45)])
    p = _poller(get, lambda: T0)
    said = []
    p.sleep = lambda s: said.append(poll.ensure(spawn=lambda: pytest.fail("spawned"),
                                                now=T0))
    assert p.run(max_ticks=5) == 0
    assert "stop" in said[0] and "switched off" in said[0]
    assert poll.heartbeat()["state"] == "stopped: asked to"
    assert [c for c in get.calls if c[0] == "odds"] == [("odds", 1)]   # one, then none


def test_ensure_starts_a_missing_loop_and_leaves_a_live_one(env, monkeypatch):
    monkeypatch.setattr(config, "POLLING_ENABLED", True)

    class P:
        pid = 4242
    started = []
    spawn = lambda: (started.append(1), P())[1]
    poll.STATE_DIR.mkdir(exist_ok=True)
    poll.RECORD.write_text("")
    assert "started" in poll.ensure(spawn=spawn, now=T0)
    poll.HEARTBEAT.write_text(json.dumps({"state": "running", "beat_at": T0.isoformat(),
                                          "pid": 7, "code_sha": db.code_sha()}))
    assert "running" in poll.ensure(spawn=spawn, now=T0 + dt.timedelta(minutes=2))
    assert "started" in poll.ensure(spawn=spawn, now=T0 + dt.timedelta(minutes=20))
    assert len(started) == 2


def test_the_loop_runs_only_against_the_ledger_of_record(env, monkeypatch, capsys):
    # The limits count one data folder's ledger; every checkout spends the
    # same paid key. A loop in a second folder would start again from zero.
    monkeypatch.setattr(config, "POLLING_ENABLED", True)
    for name in ("Poller", "OddsSource"):
        monkeypatch.setattr(poll, name, lambda *a, **k: pytest.fail("a loop was built"))

    class P:
        pid = 4242
    started = []
    spawn = lambda: (started.append(1), P())[1]               # noqa: E731
    assert poll.live() == 2
    assert "ledger of record" in capsys.readouterr().out
    assert poll.ensure(spawn=spawn, now=T0).startswith("NOT started") and not started
    record = env / "scanner" / "ledger-of-record"               # the owner, by hand
    record.parent.mkdir(exist_ok=True)
    record.write_text("")
    assert poll.ensure(spawn=spawn, now=T0).startswith("started") and started == [1]


def test_ensure_leaves_a_loop_stopped_by_a_refused_key(env, monkeypatch):
    monkeypatch.setattr(config, "POLLING_ENABLED", True)
    poll.STATE_DIR.mkdir(exist_ok=True)
    poll.HEARTBEAT.write_text(json.dumps({"state": "stopped: 401", "stop_kind": "api",
                                          "beat_at": T0.isoformat()}))
    started = []
    assert "NOT restarted" in poll.ensure(spawn=lambda: started.append(1), now=T0)
    assert not started
