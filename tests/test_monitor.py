"""The monitor's levels and thresholds."""
import datetime as dt

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


def test_scanner_checks_are_silent_until_the_scanner_exists(monkeypatch):
    monkeypatch.setattr(config, "POLLING_ENABLED", False)
    assert monitor.scanner_checks(None, NOW, {"games", "bets"}) == []


def test_a_dead_polling_loop_is_an_error_once_polling_is_on(con, monkeypatch):
    monkeypatch.setattr(config, "POLLING_ENABLED", True)
    f = [x for x in monitor.scanner_checks(con, NOW, _tables(con))
         if x["check"] == "the polling loop is running"][0]
    assert f["level"] == "ERROR" and "never" in f["detail"]
