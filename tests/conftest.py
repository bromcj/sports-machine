"""Shared setup. These tests touch NO database and NO network.

audit.py already checks the live system against live data. This suite is the
other half: pure functions, pinned to the concrete cases that actually went
wrong, so a regression fails in CI on a fresh checkout with no data/ at all.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
