"""Guardrail flags must be checked for the side actually bet.

bets/paper.py and model/predict.py used to check the starter of the side the
model favours (home if p >= 0.5), while evaluate() bets the side with the
larger edge. Bet 23 had home probability 0.5626 and went away at +210, so
the home starter's flags decided an away bet.
"""
import random

from bets.engine import evaluate, likely_side


def test_the_favoured_side_and_the_bet_side_can_differ():
    # Real bet 23: model 0.5626 home, market ~0.69 home -> the edge is away.
    assert likely_side(0.5626, 210, -250) == "away"


def test_likely_side_is_the_side_evaluate_bets():
    rng = random.Random(7)
    checked = 0
    for _ in range(2000):
        p = rng.uniform(0.3, 0.7)
        away = rng.choice([-1, 1]) * rng.randint(101, 250)
        home = -away if abs(away) > 105 else -110
        r = evaluate("mlb", p, away, home, 1000, allow_unvalidated=True)
        if r["bet"]:
            checked += 1
            assert r["side"] == likely_side(p, away, home)
    assert checked > 100


def test_a_flag_on_the_other_team_no_longer_blocks_the_bet():
    # Before: flags for home (the favoured side) blocked an away bet.
    side = likely_side(0.5626, 210, -250)
    flags = {"sp_unconfirmed": side == "home"}      # only the home SP is missing
    r = evaluate("mlb", 0.5626, 210, -250, 1000, flags=flags,
                 allow_unvalidated=True)
    assert r["bet"] and r["side"] == "away"
