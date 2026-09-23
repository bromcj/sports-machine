"""Ingest rules. Each case is something that cannot be true."""
import pytest
from ingest import quality


@pytest.mark.parametrize("value", [0, -50, 99, -99, 500_000, "abc"])
def test_impossible_moneylines_are_rejected(value):
    assert quality.moneyline(value) is not None


@pytest.mark.parametrize("value", [-110, +150, -100, +100, -750, +460, None, ""])
def test_legitimate_moneylines_are_kept(value):
    assert quality.moneyline(value) is None


def test_a_team_cannot_play_itself():
    assert quality.teams("NYY", "NYY") is not None


def test_a_final_must_have_a_score():
    # Both feeds report a POSTPONEMENT as finished. This is the rule that
    # stops one being stored as a result.
    assert quality.game("A", "B", "2026-09-22", None, None, "final") is not None


def test_an_mlb_final_cannot_be_nil_nil():
    # The archive held TOR @ BAL 2026-09-22 as a 0-0 FINAL. Extra innings
    # mean MLB cannot produce that.
    assert quality.game("A", "B", "2026-09-22", 0, 0, "final") is not None


def test_a_real_final_and_a_postponement_are_both_kept():
    assert quality.game("A", "B", "2026-09-22", 3, 2, "final") is None
    assert quality.game("A", "B", "2026-09-22", None, None, "postponed") is None
