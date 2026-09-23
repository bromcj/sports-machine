"""A paper bet must be one somebody could actually have placed (C5)."""
import datetime as dt
from bets.paper import PLACE_CUTOFF_MIN


def placeable(now, start, status, snapshot_ts):
    """The rule place() applies, isolated from the database."""
    if start is None or status != "scheduled":
        return False
    if (start - now).total_seconds() / 60 < PLACE_CUTOFF_MIN:
        return False
    return snapshot_ts is not None and snapshot_ts <= now < start


def test_a_game_safely_ahead_is_placeable():
    now = dt.datetime(2026, 9, 23, 18, 0, tzinfo=dt.timezone.utc)
    assert placeable(now, now + dt.timedelta(hours=4), "scheduled",
                     now - dt.timedelta(hours=6))


def test_a_game_already_under_way_is_not():
    # Its CLV would be exactly 0 by construction: the price used to place it
    # is the same snapshot used to grade it.
    now = dt.datetime(2026, 9, 23, 18, 0, tzinfo=dt.timezone.utc)
    assert not placeable(now, now - dt.timedelta(hours=2), "live",
                         now - dt.timedelta(hours=6))


def test_a_game_inside_the_cutoff_is_not():
    now = dt.datetime(2026, 9, 23, 18, 0, tzinfo=dt.timezone.utc)
    start = now + dt.timedelta(minutes=PLACE_CUTOFF_MIN - 1)
    assert not placeable(now, start, "scheduled", now - dt.timedelta(hours=6))


def test_a_price_taken_after_first_pitch_is_not_a_bet():
    now = dt.datetime(2026, 9, 23, 18, 0, tzinfo=dt.timezone.utc)
    start = now + dt.timedelta(hours=4)
    assert not placeable(now, start, "scheduled", start + dt.timedelta(minutes=1))


def test_a_game_with_no_known_start_is_refused():
    now = dt.datetime(2026, 9, 23, 18, 0, tzinfo=dt.timezone.utc)
    assert not placeable(now, None, "scheduled", now)
