"""The polling loop, driven by a fake clock and a fake API. No network: every
HTTP call goes to FakeGet, which also proves no call happens before the budget
has been checked."""
import datetime as dt
import json

import pytest
import requests

import config
import db
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


def test_a_stop_file_ends_the_loop(env):
    get = FakeGet([T0 + dt.timedelta(days=3)])
    p = _poller(get, lambda: T0)
    ticks = []
    p.sleep = lambda s: (ticks.append(s), poll.STOP.write_text("stop"))
    assert p.run(max_ticks=10) == 0
    assert len(ticks) == 1 and poll.heartbeat()["state"] == "stopped: asked to"


# ------------------------------------------------------------ supervisor ---

def test_ensure_does_nothing_while_polling_is_switched_off(env, monkeypatch):
    monkeypatch.setattr(config, "POLLING_ENABLED", False)
    started = []
    msg = poll.ensure(spawn=lambda: started.append(1))
    assert not started and "switched off" in msg
    assert poll.live() == 2                                   # --live refuses too


def test_ensure_starts_a_missing_loop_and_leaves_a_live_one(env, monkeypatch):
    monkeypatch.setattr(config, "POLLING_ENABLED", True)

    class P:
        pid = 4242
    started = []
    spawn = lambda: (started.append(1), P())[1]
    assert "started" in poll.ensure(spawn=spawn, now=T0)
    poll.STATE_DIR.mkdir(exist_ok=True)
    poll.HEARTBEAT.write_text(json.dumps({"state": "running", "beat_at": T0.isoformat(),
                                          "pid": 7, "code_sha": db.code_sha()}))
    assert "running" in poll.ensure(spawn=spawn, now=T0 + dt.timedelta(minutes=2))
    assert "started" in poll.ensure(spawn=spawn, now=T0 + dt.timedelta(minutes=20))
    assert len(started) == 2


def test_ensure_leaves_a_loop_stopped_by_a_refused_key(env, monkeypatch):
    monkeypatch.setattr(config, "POLLING_ENABLED", True)
    poll.STATE_DIR.mkdir(exist_ok=True)
    poll.HEARTBEAT.write_text(json.dumps({"state": "stopped: 401", "stop_kind": "api",
                                          "beat_at": T0.isoformat()}))
    started = []
    assert "NOT restarted" in poll.ensure(spawn=lambda: started.append(1), now=T0)
    assert not started
