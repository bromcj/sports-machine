"""Odds conversion, de-vig and CLV."""
from bets.engine import american_to_decimal, novig_probs, clv_pct


def test_american_to_decimal_both_signs():
    assert american_to_decimal(+100) == 2.0
    assert american_to_decimal(-100) == 2.0
    assert abs(american_to_decimal(+150) - 2.5) < 1e-12
    assert abs(american_to_decimal(-200) - 1.5) < 1e-12


def test_devig_is_exact_on_a_balanced_book():
    a, h = novig_probs(-110, -110)
    assert abs(a - 0.5) < 1e-12 and abs(h - 0.5) < 1e-12


def test_devig_always_sums_to_one():
    for away, home in [(+150, -170), (-250, +210), (+100, -100), (-750, +460)]:
        a, h = novig_probs(away, home)
        assert abs(a + h - 1) < 1e-12, (away, home)


def test_devig_removes_the_margin_rather_than_ignoring_it():
    # -110/-110 implies 52.38% each, 104.76% total. The margin is the 4.76%.
    a, h = novig_probs(-110, -110)
    assert a + h == 1.0


def test_clv_sign_is_the_right_way_round():
    # Taking +120 and closing at +100 is a BETTER price than the close.
    assert clv_pct(+120, +100) > 0
    assert clv_pct(+100, +120) < 0
