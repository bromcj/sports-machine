"""The scoreboard's aggregation helpers: mean, spread and the bootstrap
interval. Entry and grading run against a real database in
tests/test_manual_entry.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import pytest

from bets.manual import _mean, _stdev, _boot_ci


def test_mean_ignores_missing_values_rather_than_treating_them_as_zero():
    assert _mean([0.1, None, 0.3]) == pytest.approx(0.2)
    assert _mean([None, None]) is None
    assert _mean([]) is None


def test_stdev_needs_two_points():
    assert _stdev([0.5]) == 0.0
    assert _stdev([]) == 0.0
    assert _stdev([1.0, 3.0]) == pytest.approx(1.4142135, rel=1e-6)


def test_bootstrap_ci_brackets_the_mean_and_is_deterministic():
    xs = [0.2, -0.1, 0.4, -0.3, 0.1, 0.05, -0.2, 0.3]
    lo1, hi1 = _boot_ci(xs)
    lo2, hi2 = _boot_ci(xs)
    assert (lo1, hi1) == (lo2, hi2)          # seeded: a rerun reproduces it
    assert lo1 < sum(xs) / len(xs) < hi1


def test_bootstrap_ci_on_no_data_is_nan_not_zero():
    lo, hi = _boot_ci([])
    assert lo != lo and hi != hi             # NaN, so it cannot read as 0%
