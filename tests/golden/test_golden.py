"""The golden test, as pytest. See capture.py for what it pins and why.

Skips when `data_phase1/` is absent, which is the normal state everywhere
except a Phase-1 working tree: CI has no database, and a test that cannot run
should say so rather than pass silently.
"""
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
ROOT = HERE.parent.parent

import capture  # noqa: E402


pytestmark = pytest.mark.skipif(
    not (ROOT / "data_golden").exists()
    or not (HERE / "baseline").exists(),
    reason="golden baseline or data_phase1/ copy not present")


def test_nothing_moved():
    """Every captured behaviour reproduces the baseline exactly."""
    bad = capture.compare(with_audit=False)
    assert not bad, (
        f"{len(bad)} golden difference(s):\n  " + "\n  ".join(bad[:25]))


def test_tolerance_is_not_quietly_wide():
    """The float tolerance is a visible constant and stays at or under 1e-9."""
    assert capture.TOLERANCE <= 1e-9
