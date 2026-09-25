"""The credit limits and the polling cadence. No network: the ledger is a temp
database and every clock is passed in."""
import datetime as dt
import random

import pytest

import config
import db
from feeds import ET
from scanner import budget
from scanner.budget import OverBudget

UTC = dt.timezone.utc
NOON = dt.datetime(2026, 10, 14, 16, 0, tzinfo=UTC)        # 12:00 ET


@pytest.fixture
def con(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init()
    capsys.readouterr()
    c = db.connect()
    yield c
    c.close()


def _spend(con, credits, when, cost=True):
    budget.record(con, consumer="poll", endpoint="odds", estimated=credits,
                  cost=credits if cost else None, ok=cost, ts=when)


# ------------------------------------------------------------------- cost ---

def test_a_call_costs_markets_times_one_region_per_ten_books():
    assert budget.call_cost(3, 10) == 3          # the scanner's own list
    assert budget.call_cost(3, 11) == 6
    assert budget.call_cost(2, 15) == 4          # the props collector, measured
    assert budget.call_cost(1, 4) == 1           # the cloud's pull, measured
    assert budget.call_cost() == len(config.ODDS_MARKETS) * 1


# ----------------------------------------------------------------- limits ---

def test_the_brief_cap_stops_a_call_before_it_is_made(con, monkeypatch):
    monkeypatch.setattr(config, "CREDIT_CAP_BRIEF", 100)
    monkeypatch.setattr(config, "BRIEF_POLL_DAYS", 1)
    _spend(con, 98, NOON - dt.timedelta(hours=1))
    with pytest.raises(OverBudget, match="brief"):
        budget.check(con, 3, NOON)
    budget.check(con, 2, NOON)                   # exactly to the cap is allowed


def test_the_monthly_budget_stops_a_call(con, monkeypatch):
    monkeypatch.setattr(config, "BRIEF_ACTIVE", False)
    monkeypatch.setattr(config, "POLL_MONTHLY_BUDGET", 50)
    _spend(con, 48, dt.datetime(2026, 10, 2, 16, tzinfo=UTC))
    with pytest.raises(OverBudget, match="month"):
        budget.check(con, 3, NOON)
    # Last month's spending does not count against this one.
    budget.record(con, consumer="poll", endpoint="odds", estimated=0, cost=0,
                  ok=True, ts=NOON)
    assert budget.status(con, dt.datetime(2026, 11, 2, 16, tzinfo=UTC))["month_spent"] == 0


def test_the_daily_pace_spreads_the_brief_and_rolls_unspent_forward(con, monkeypatch):
    monkeypatch.setattr(config, "CREDIT_CAP_BRIEF", 420)
    monkeypatch.setattr(config, "BRIEF_POLL_DAYS", 42)
    s = budget.status(con, NOON)
    assert s["allowance_today"] == pytest.approx(10.0)          # 420 / 42
    _spend(con, 9, NOON)
    with pytest.raises(OverBudget, match="pace"):
        budget.check(con, 3, NOON)
    # Next day: 411 left over 41 days still to poll.
    s = budget.status(con, NOON + dt.timedelta(days=1))
    assert s["allowance_today"] == pytest.approx(411 / 41)


def test_after_the_brief_the_month_is_spread_over_its_days(con, monkeypatch):
    # After the brief the month's pace is the only daily limit. Every other
    # pace test runs with the brief on, whose pace is the tighter one.
    monkeypatch.setattr(config, "BRIEF_ACTIVE", False)
    monkeypatch.setattr(config, "POLL_MONTHLY_BUDGET", 310)
    assert budget.status(con, NOON)["allowance_today"] == pytest.approx(310 / 18)  # Oct 14-31
    _spend(con, 15, NOON - dt.timedelta(hours=1))
    with pytest.raises(OverBudget) as e:
        budget.check(con, 3, NOON)               # 18 > 17.2, with 295 left in the month
    assert e.value.limit == "pace"
    budget.check(con, 2, NOON)                   # 17 fits
    # Tomorrow the unspent part rolls forward: 295 over the 17 days left.
    assert budget.status(con, NOON + dt.timedelta(days=1))["allowance_today"] == \
        pytest.approx(295 / 17)


def test_once_the_briefs_polling_days_are_used_its_pace_is_zero(con, monkeypatch):
    # The pace is what is left over the polling days left. Past the last
    # planned day that was "everything left, today", so a bug could spend the
    # rest of the brief in one day. Now it refuses until the owner extends
    # BRIEF_POLL_DAYS by a commit.
    monkeypatch.setattr(config, "CREDIT_CAP_BRIEF", 6000)
    monkeypatch.setattr(config, "BRIEF_POLL_DAYS", 42)
    day = dt.datetime(2026, 11, 30, 16, tzinfo=UTC)          # also a month's last day
    for d in range(1, 42):
        _spend(con, 3, day - dt.timedelta(days=d))
    # The 42nd planned day may still use what is left, as before.
    assert budget.status(con, day)["allowance_today"] == pytest.approx(6000 - 41 * 3)
    _spend(con, 3, day - dt.timedelta(days=42))              # a 43rd day
    s = budget.status(con, day)
    assert (s["brief_days_left"], s["allowance_today"]) == (0, 0)
    with pytest.raises(OverBudget, match="BRIEF_POLL_DAYS") as e:
        budget.check(con, 3, day)
    assert e.value.limit == "brief"


def test_a_call_that_failed_counts_at_its_estimate(con, monkeypatch):
    monkeypatch.setattr(config, "CREDIT_CAP_BRIEF", 10)
    monkeypatch.setattr(config, "BRIEF_POLL_DAYS", 1)
    _spend(con, 9, NOON, cost=False)
    assert budget.spent(con) == 9
    with pytest.raises(OverBudget):
        budget.check(con, 3, NOON)


def test_the_et_day_is_the_unit_not_the_utc_day(con):
    # 23:30 ET on the 13th is 03:30 UTC on the 14th. It belongs to the 13th.
    late = dt.datetime(2026, 10, 14, 3, 30, tzinfo=UTC)
    _spend(con, 5, late)
    assert budget.status(con, NOON)["today_spent"] == 0
    assert budget.status(con, late)["today_spent"] == 5


# ---------------------------------------------------------------- cadence ---

def test_the_brief_cadence_by_time_to_start():
    assert [budget.interval(0, m) for m in (1, 90, 91, 360, 361, 1440, 1441, 10000)] == [
        2, 2, 5, 5, 15, 15, 60, 60]
    assert budget.interval(0, 0) is None          # started: stop
    assert budget.interval(0, -30) is None


def test_nothing_is_polled_once_every_game_has_started():
    start = NOON + dt.timedelta(hours=1)
    assert budget.due([start], NOON, None, 0)
    assert not budget.due([start], start, None, 0)
    assert not budget.due([], NOON, None, 0)


def test_one_game_at_the_last_level_is_polled_three_times():
    start = NOON + dt.timedelta(hours=5)
    times = budget.poll_times({"nba": [start]}, NOON, start + dt.timedelta(hours=1), 6)
    assert [(start - t).total_seconds() / 60 for t in times["nba"]] == [90, 60, 30]


def test_every_level_that_polls_prices_every_game_in_its_last_30_minutes():
    rng = random.Random(7)
    day = dt.datetime(2026, 11, 3, 0, 0, tzinfo=ET)
    for trial in range(20):
        sched = {sp: sorted(day + dt.timedelta(minutes=rng.randrange(11 * 60, 23 * 60))
                            for _ in range(rng.randrange(1, 12)))
                 for sp in ("nfl", "nba", "nhl")}
        for level in range(len(budget.LADDER)):
            times = budget.poll_times(sched, day, day + dt.timedelta(days=1), level)
            for sp, starts in sched.items():
                for s in starts:
                    before = [t for t in times[sp] if t < s]
                    assert before and (s - before[-1]).total_seconds() <= 30 * 60, (
                        trial, level, sp, s)


def test_the_governor_takes_the_fastest_level_the_budget_covers():
    day = dt.datetime(2026, 11, 3, 0, 0, tzinfo=ET)
    sched = budget.typical_schedule(["nba"], dt.date(2026, 11, 2))
    end = day + dt.timedelta(days=1)
    need = [sum(budget.project(sched, day, end, lv).values()) * 3
            for lv in range(len(budget.LADDER))]
    assert need == sorted(need, reverse=True)            # slower is cheaper
    assert budget.choose_level(sched, day, need[0], cost=3, end=end) == 0
    assert budget.choose_level(sched, day, need[3], cost=3, end=end) == 3
    assert budget.choose_level(sched, day, need[3] - 1, cost=3, end=end) == 4
    assert budget.choose_level(sched, day, need[-1] - 1, cost=3, end=end) is None


def test_the_plan_stays_inside_both_limits():
    # A-V3 in docs/experiments.md.
    r = budget.plan(out=lambda *_: None)
    assert r["brief_projection"] <= config.CREDIT_CAP_BRIEF
    assert r["normal_month"] + 3000 <= 15000
    assert r["ideal_month"] > 15000          # and the brief's own cadence does not fit
