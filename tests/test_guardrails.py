"""Guardrails must actually be able to refuse a bet."""
from bets.engine import MAX_DAILY_PCT, MAX_STAKE_PCT, evaluate


def test_a_guardrail_flag_blocks_the_bet():
    # No caller ever passed flags, so this path had never once run.
    clear = evaluate("mlb", 0.75, +150, -170, 1000, allow_unvalidated=True)
    assert clear["bet"] is True
    flagged = evaluate("mlb", 0.75, +150, -170, 1000,
                       flags={"sp_unconfirmed": True}, allow_unvalidated=True)
    assert flagged["bet"] is False and "guardrail" in flagged["reason"]


def test_an_unrelated_flag_does_not_block():
    r = evaluate("mlb", 0.75, +150, -170, 1000,
                 flags={"qb_unconfirmed": True}, allow_unvalidated=True)
    assert r["bet"] is True


def test_the_daily_cap_refuses_once_it_is_spent():
    r = evaluate("mlb", 0.75, +150, -170, 1000, allow_unvalidated=True,
                 exposure_used=MAX_DAILY_PCT)
    assert r["bet"] is False and "daily exposure" in r["reason"]


def test_the_daily_cap_shrinks_the_last_stake_rather_than_overshooting():
    # Fifteen bets at the 3% per-bet maximum is 45% of bankroll on one night.
    almost = MAX_DAILY_PCT - 0.005
    r = evaluate("mlb", 0.90, +150, -170, 1000, allow_unvalidated=True,
                 exposure_used=almost)
    assert r["bet"] is True
    assert r["stake"] <= 1000 * 0.005 + 1e-9, r["stake"]


def test_a_normal_bet_is_still_capped_per_bet():
    r = evaluate("mlb", 0.99, +150, -170, 1000, allow_unvalidated=True)
    assert r["stake"] <= 1000 * MAX_STAKE_PCT + 1e-9


def test_innings_come_from_outs_not_batters_faced():
    # Seven batters: two singles, a walk and four outs is 1.33 innings, not
    # the 2.33 that batters-faced / 3 claimed.
    import pandas as pd
    from bets.guardrails import innings_per_start
    sc = pd.DataFrame({"pitcher": [7] * 7, "game_pk": [1] * 7,
                       "events": ["single", "strikeout", "walk", "field_out",
                                  "single", "grounded_into_double_play", None]})
    assert abs(innings_per_start(sc, 7, [1]) - 4 / 3) < 1e-12
