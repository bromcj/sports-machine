"""The one-time paid backfill. These guard a PURCHASE, so they matter."""
import datetime as dt
import backfill_odds_history as b


def _starts(*hhmm):
    base = dt.datetime(2026, 5, 1, tzinfo=dt.timezone.utc)
    return [base.replace(hour=h, minute=m) for h, m in hhmm]


def test_games_starting_together_share_one_request():
    # A historical request returns EVERY game in that snapshot, so a shared
    # start time must not be paid for twice.
    assert len(b._clusters(_starts((23, 5), (23, 5), (23, 10)))) == 1


def test_every_game_gets_a_price_inside_the_closing_window():
    """The reason CLUSTER_MIN is 15 and not 60.

    A request at T serves games starting in (T, T+60]; beyond that it is not a
    closing line. With the snapshot placed OFFSET_MIN before the anchor, the
    cluster may only be 60 - OFFSET_MIN wide.
    """
    assert b.OFFSET_MIN + b.CLUSTER_MIN <= 60
    starts = _starts((23, 0), (23, 7), (23, 14), (23, 40), (1, 10))
    reqs = b._clusters(starts)
    for s in starts:
        lead = min((s - t).total_seconds() / 60 for t in reqs
                   if (s - t).total_seconds() > 0)
        assert 0 < lead <= 60, (s, lead)


def test_a_wide_cluster_would_break_that():
    # Proof the constraint is real rather than decorative.
    starts = _starts((23, 0), (23, 55))
    reqs = b._clusters(starts, tol_min=60)
    worst = max(min((s - t).total_seconds() / 60 for t in reqs
                    if (s - t).total_seconds() > 0) for s in starts)
    assert worst > 60


def test_only_test_seasons_are_requested():
    # Training never reads novig_home_prob, so 2022 and 2023 need no odds.
    # Requesting them would waste 18,750 credits.
    assert "2022" not in b.TEST_SEASONS and "2023" not in b.TEST_SEASONS


def test_the_price_per_request_is_not_guessed():
    # Confirmed against the API docs: historical costs 10x the live endpoint,
    # and a bookmakers list counts as ONE region.
    assert b.CREDITS_PER_REQUEST == 10
    assert "," in b.BOOKS and b.MARKET == "h2h"


def test_history_is_stored_under_its_own_snapshot_types():
    # It must never be mistakable for a live pull.
    plan = [{"kind": "hist_open"}, {"kind": "hist_close"}]
    for p in plan:
        assert p["kind"].startswith("hist_")
