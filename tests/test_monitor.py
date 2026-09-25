"""The monitor's levels and thresholds."""
import datetime as dt
import json

import pytest

import config
import db
import monitor
from scanner import budget, poll


def test_alerting_levels_are_the_serious_ones():
    assert set(monitor.ALERT_AT) == {"CRITICAL", "ERROR"}


def test_a_finding_carries_everything_a_reader_needs():
    f = monitor._find("ERROR", "a check", "what happened", count=3)
    assert f["level"] == "ERROR" and f["check"] and f["detail"]
    assert f["count"] == 3


def test_thresholds_are_sane():
    # A game should be final long before 12h; a bet should settle by the next
    # nightly run.
    assert monitor.STALE_GAME_HOURS <= 24
    assert monitor.UNSETTLED_BET_HOURS <= 48
    # Feeds disagree by minutes on a delay, never by hours.
    assert 60 <= monitor.FEED_GAP_MIN <= 360
    lo, hi = monitor.HOME_PROB_RANGE
    assert 0.48 <= lo < hi <= 0.60
    assert monitor.CREDITS_CRIT < monitor.CREDITS_WARN < 1.0


# ------------------------------------------------------------- the scanner ---

NOW = dt.datetime(2026, 11, 3, 16, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def con(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(poll, "HEARTBEAT", tmp_path / "poll.json")
    monkeypatch.setattr(poll, "LOCK", tmp_path / "poll.lock")
    monkeypatch.setattr(config, "CREDIT_CAP_BRIEF", 1000)
    monkeypatch.setattr(config, "POLL_MONTHLY_BUDGET", 100000)
    db.init()
    capsys.readouterr()
    c = db.connect()
    yield c
    c.close()


def _tables(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _level(con, spent):
    budget.record(con, consumer="poll", endpoint="odds", estimated=spent, cost=spent,
                  ok=True, ts=NOW - dt.timedelta(days=1))
    f = [x for x in monitor.scanner_checks(con, NOW, _tables(con))
         if x["check"] == "scanner credits: the brief's cap"][0]
    return f["level"]


def test_the_brief_cap_alerts_at_25_and_10_percent_left(con):
    assert _level(con, 700) == "INFO"          # 30% left
    assert _level(con, 60) == "WARNING"        # 24% left
    assert _level(con, 160) == "CRITICAL"      # 8% left


def test_the_briefs_planned_polling_days_used_up_is_an_error(con, monkeypatch):
    # Past the brief's 42 planned polling days the pace is 0 and the loop
    # pauses, making no metered call, until the owner extends BRIEF_POLL_DAYS.
    # Every finding stayed INFO ("126 of 6,000 spent (98% left)", the loop
    # "running"), so polling stopped with nothing said.
    monkeypatch.setattr(config, "BRIEF_ACTIVE", True)
    monkeypatch.setattr(config, "CREDIT_CAP_BRIEF", 6000)
    monkeypatch.setattr(config, "BRIEF_POLL_DAYS", 42)

    def days_used():
        return [(x["level"], x["detail"]) for x in monitor.scanner_checks(con, NOW, _tables(con))
                if x["check"] == "scanner credits: the brief's polling days"]
    for d in range(1, 42):
        budget.record(con, consumer="poll", endpoint="odds", estimated=3, cost=3, ok=True,
                      ts=NOW - dt.timedelta(days=d))
    assert days_used() == []                     # the 42nd day may still poll
    budget.record(con, consumer="poll", endpoint="odds", estimated=3, cost=3, ok=True,
                  ts=NOW - dt.timedelta(days=42))
    assert days_used() == [("ERROR", "the loop makes no metered call: the brief's 42 planned"
                                     " polling days are used (126 of 6,000 spent); to keep"
                                     " polling, extend config.BRIEF_POLL_DAYS by a commit")]


def test_scanner_checks_are_silent_until_the_scanner_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "POLLING_ENABLED", False)
    monkeypatch.setattr(poll, "HEARTBEAT", tmp_path / "poll.json")
    monkeypatch.setattr(poll, "LOCK", tmp_path / "poll.lock")
    assert monitor.scanner_checks(None, NOW, {"games", "bets"}) == []


def test_a_live_loop_while_polling_is_off_is_an_error(con, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "POLLING_ENABLED", False)
    monkeypatch.setattr(poll, "STOP", tmp_path / "poll.stop")
    loop = [x for x in monitor.scanner_checks(con, NOW, _tables(con))
            if "loop" in x["check"]]
    assert loop == []                            # no heartbeat: silent, as today
    poll.HEARTBEAT.write_text(json.dumps({"state": "running", "pid": 7,
                                          "beat_at": NOW.isoformat()}))
    loop = [x for x in monitor.scanner_checks(con, NOW, _tables(con))
            if "loop" in x["check"]]
    assert [x["level"] for x in loop] == ["ERROR"]
    assert "running while polling is switched off" in loop[0]["detail"]


@pytest.mark.parametrize("on, level", [(True, "INFO"), (False, "ERROR")])
def test_a_loop_on_its_first_tick_holds_the_lock_before_any_beat(con, monkeypatch,
                                                                 tmp_path, on, level):
    monkeypatch.setattr(config, "POLLING_ENABLED", on)
    monkeypatch.setattr(poll, "STATE_DIR", tmp_path)
    held = poll.take_lock()                      # as `poll --live` does; no heartbeat yet
    try:
        loop = [x for x in monitor.scanner_checks(con, NOW, _tables(con))
                if "loop" in x["check"]]
    finally:
        held.close()
    assert [x["level"] for x in loop] == [level]


def _loop_findings(con, monkeypatch, tmp_path, hb):
    """The loop findings while this process holds the lock, as `poll --live`
    does, with this heartbeat (None: no heartbeat file)."""
    monkeypatch.setattr(poll, "STATE_DIR", tmp_path)
    if hb is not None:
        poll.HEARTBEAT.write_text(json.dumps(hb))
    held = poll.take_lock()
    try:
        return [(x["level"], x["detail"]) for x in
                monitor.scanner_checks(con, NOW, _tables(con)) if "loop" in x["check"]]
    finally:
        held.close()


@pytest.mark.parametrize("age_min, level", [(7, "INFO"), (16, "ERROR"), (480, "ERROR")])
def test_a_loop_that_holds_its_lock_but_stopped_beating_is_an_error(con, monkeypatch,
                                                                    tmp_path, age_min, level):
    # A request that never returns, or a console paused by a click, keeps the
    # lock held while the beat ages. Called "running", closing prices stopped
    # with no alert, even 8 hours on. A slow but live tick - every request
    # timing out, 378 s, then the 30 s sleep - beats again inside 7 minutes.
    monkeypatch.setattr(config, "POLLING_ENABLED", True)
    loop = _loop_findings(con, monkeypatch, tmp_path, {
        "state": "running", "pid": 4321, "level": 3,
        "beat_at": (NOW - dt.timedelta(minutes=age_min)).isoformat()})
    assert [lv for lv, _ in loop] == [level]
    if level == "ERROR":
        assert f"has not beaten for {age_min} min - it may be hung; end pid 4321" in loop[0][1]


@pytest.mark.parametrize("hb", [None, {"state": "stopped: asked to", "stop_kind": "asked",
                                       "pid": 7, "level": 2, "beat_at": "2026-11-03T08:00:00Z"}])
def test_a_loop_before_its_own_first_beat_says_so(con, monkeypatch, tmp_path, hb):
    # No heartbeat, or the last loop's 'stopped' one: nothing about this loop
    # yet. It said 'None (level None)', or 'stopped: asked to (level 2)'.
    monkeypatch.setattr(config, "POLLING_ENABLED", True)
    assert _loop_findings(con, monkeypatch, tmp_path, hb) == [
        ("INFO", "first tick, no beat yet")]


def test_a_dead_polling_loop_is_an_error_once_polling_is_on(con, monkeypatch):
    monkeypatch.setattr(config, "POLLING_ENABLED", True)
    f = [x for x in monitor.scanner_checks(con, NOW, _tables(con))
         if x["check"] == "the polling loop is running"][0]
    assert f["level"] == "ERROR" and "never" in f["detail"]
