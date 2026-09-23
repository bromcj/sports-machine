"""The gate arithmetic: pooled SE, leave-one-season-out, record_paper."""
import random
import pytest
from model.validation import (MIN_PAPER_BETS, PAPER_CLV_SIGMA, SLOTS,
                              _pooled_diff, _stdev, leave_one_season_out,
                              slot_of)


def _seasons(margins, n=2170, sd=0.14):
    return [{"season": 2024 + i, "logloss_market": 0.69,
             "logloss_model": 0.69 - m, "n_games": n, "ll_diff_sd": sd}
            for i, m in enumerate(margins)]


def test_pooled_se_matches_a_direct_computation():
    # Pooling from (n, mean, sd) must equal computing on the concatenated
    # per-game values, or the gate is testing a different number than it says.
    import numpy as np
    rng = np.random.default_rng(3)
    arrs = [rng.normal(0.004, 0.13, 2179), rng.normal(0.002, 0.13, 2187),
            rng.normal(0.001, 0.13, 2131)]
    rows = [{"season": 2024 + i, "logloss_market": 0.0,
             "logloss_model": -a.mean(), "n_games": len(a),
             "ll_diff_sd": float(a.std(ddof=1))} for i, a in enumerate(arrs)]
    got = _pooled_diff(rows)
    allv = np.concatenate(arrs)
    assert abs(got["mean"] - allv.mean()) < 1e-9
    assert abs(got["se"] - allv.std(ddof=1) / len(allv) ** 0.5) < 1e-9


def test_pooling_refuses_without_per_game_spread():
    rows = [{"season": 2024, "logloss_market": .69, "logloss_model": .68}]
    assert _pooled_diff(rows) is None


def test_leave_one_season_out_finds_a_result_carried_by_one_year():
    # MLB's real shape: one strong season, two that are nothing.
    r = leave_one_season_out(_seasons([0.016, 0.0001, 0.0001]))
    assert r["worst_season"] == 2024
    assert r["worst_t"] < 0.5


def test_leave_one_season_out_is_content_with_a_consistent_edge():
    r = leave_one_season_out(_seasons([0.005, 0.005, 0.005]))
    assert r["worst_t"] > 2.0


def test_stdev_handles_degenerate_input():
    assert _stdev([]) == 0.0
    assert _stdev([5]) == 0.0
    assert abs(_stdev([1, 2, 3, 4]) - 1.2909944) < 1e-6


@pytest.mark.parametrize("hour,expected", [
    (12, "day"), (16, "day"), (17, "evening"), (20, "evening"),
    (21, "late"), (23, "late")])
def test_slate_slots(hour, expected):
    assert slot_of(hour) == expected


def test_a_zero_skill_model_rarely_clears_the_info_bar():
    # The old gate - "is the average above zero" - passed this 50% of the
    # time. It was not a weak test, it was no test.
    rng = random.Random(11)
    hits = 0
    for _ in range(400):
        noise = [rng.gauss(0, 2.965) for _ in range(MIN_PAPER_BETS)]
        mean = sum(noise) / len(noise)
        se = _stdev(noise) / len(noise) ** 0.5
        if mean - PAPER_CLV_SIGMA * se > 0:
            hits += 1
    assert hits / 400 < 0.05


def test_slots_vocabulary_is_stable():
    assert SLOTS == ("day", "evening", "late")
