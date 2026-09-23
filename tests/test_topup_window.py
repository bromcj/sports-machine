"""The Statcast top-up window (C4) and the pregame guard (C5)."""
import datetime as dt
from backfill import RESETTLE_DAYS, SEASON_DATES


def window(last: str, today: str, year: int = 2026):
    """The expression topup_statcast uses, isolated from the network."""
    target = dt.date.fromisoformat(today)
    season_end = dt.date.fromisoformat(SEASON_DATES[year][1])
    if year == dt.date.today().year:
        season_end = max(season_end, target)
    end = min(target - dt.timedelta(days=1), season_end)
    start = max(dt.date.fromisoformat(last) - dt.timedelta(days=RESETTLE_DAYS - 1),
                dt.date.fromisoformat(SEASON_DATES[year][0]))
    return start, end


def test_it_never_asks_for_today():
    # Asking for today downloads games still being played, and the next run
    # started at today+1 - so whatever had not been published was skipped
    # permanently.
    for last, today in [("2026-09-21", "2026-09-23"), ("2026-09-22", "2026-09-23"),
                        ("2026-09-22", "2026-09-24")]:
        _, end = window(last, today)
        assert end < dt.date.fromisoformat(today), (last, today)


def test_it_always_re_reads_recent_days():
    for last, today in [("2026-09-21", "2026-09-23"), ("2026-09-22", "2026-09-24")]:
        start, _ = window(last, today)
        assert start <= dt.date.fromisoformat(last), (last, today)


def test_it_repairs_a_parquet_that_already_contains_today():
    # The old behaviour could produce this. The window must still reach back.
    start, end = window("2026-09-23", "2026-09-23")
    assert start < end < dt.date.fromisoformat("2026-09-23")


def test_it_never_reaches_before_the_season_starts():
    start, _ = window("2026-03-26", "2026-03-27")
    assert start >= dt.date.fromisoformat(SEASON_DATES[2026][0])
