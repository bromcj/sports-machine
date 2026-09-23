"""The monitor's levels and thresholds."""
import monitor


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
