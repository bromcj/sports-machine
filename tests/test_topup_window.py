"""The Statcast top-up window, through backfill.topup_window (the real one).

This file used to hold its own copy of the expression, and would have started
failing on 2027-01-01 because the copy compared against the real year.
"""
import datetime as dt

from backfill import SEASON_DATES, topup_window

D = dt.date.fromisoformat


def window(last, today, year=2026):
    return topup_window(D(last), D(today), year, current_year=2026)


def test_it_never_asks_for_today():
    # Asking for today downloads games still being played, and the next run
    # started at today+1 - so whatever had not been published was skipped
    # permanently.
    for last, today in [("2026-09-21", "2026-09-23"), ("2026-09-22", "2026-09-23"),
                        ("2026-09-22", "2026-09-24")]:
        _, end = window(last, today)
        assert end < D(today), (last, today)


def test_it_always_re_reads_recent_days():
    for last, today in [("2026-09-21", "2026-09-23"), ("2026-09-22", "2026-09-24")]:
        start, _ = window(last, today)
        assert start <= D(last), (last, today)


def test_it_repairs_a_parquet_that_already_contains_today():
    start, end = window("2026-09-23", "2026-09-23")
    assert start < end < D("2026-09-23")


def test_it_never_reaches_before_the_season_starts():
    start, _ = window("2026-03-26", "2026-03-27")
    assert start >= D(SEASON_DATES[2026][0])


def test_a_past_season_stops_at_its_end():
    _, end = topup_window(D("2025-09-20"), D("2026-09-24"), 2025, current_year=2026)
    assert end == D(SEASON_DATES[2025][1])
