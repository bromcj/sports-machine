"""Fee models. The Kalshi cases are A-V2 in docs/experiments.md, fixed there
before this file was written."""
import pytest

from bets.engine import american_to_decimal
from scanner import fees
from scanner.fees import UnknownFee

K = fees.kalshi_key("quadratic", 1)
KM = fees.kalshi_key("quadratic_with_maker_fees", 1)


def test_kalshi_taker_fee_at_fifty_cents():
    assert fees.fee(K, 0.50, 1, conservative=False) == 0.0175
    assert fees.fee(K, 0.50, 100, conservative=False) == 1.75


def test_kalshi_fee_is_ceiled_exactly_not_in_floating_point():
    # 0.07 x 0.01 x 0.99 = 0.000693 exactly. In floating point the product is
    # a hair above it, and ceiling that to the millionth gives 0.000694. That
    # happens in 386 of 594 price/size cases tried (1-99c x six sizes).
    assert fees.fee(K, 0.01, 1, conservative=False) == 0.000693
    assert fees.fee(K, 0.02, 3, conservative=False) == 0.004116


def test_kalshi_conservative_fee_charges_the_cent_rounding():
    # One contract: 0.50 + 0.0175 leaves the balance as 0.52 for a
    # non-direct member, so 2 cents leave on the order.
    assert fees.fee(K, 0.50, 1) == pytest.approx(0.02, abs=1e-12)
    # A hundred: 50.00 + 1.75 is already whole cents.
    assert fees.fee(K, 0.50, 100) == pytest.approx(1.75, abs=1e-12)


def test_kalshi_fee_is_symmetric_and_smallest_at_the_extremes():
    assert fees.fee(K, 0.10, 100, conservative=False) == pytest.approx(0.63)
    assert fees.fee(K, 0.90, 100, conservative=False) == pytest.approx(0.63)
    assert fees.fee(K, 0.10, 100) < fees.fee(K, 0.50, 100)


def test_kalshi_maker_pays_nothing_on_a_plain_series_and_a_quarter_on_maker_series():
    assert fees.fee(K, 0.50, 100, role="maker") == 0.0
    assert fees.fee(KM, 0.50, 100, role="maker", conservative=False) == pytest.approx(0.4375)


def test_kalshi_multiplier_scales_the_fee():
    half = fees.kalshi_key("quadratic", 0.5)
    assert half == "kalshi:quadratic:0.5"
    assert fees.fee(half, 0.50, 100, conservative=False) == pytest.approx(0.875)


def test_an_unread_fee_type_refuses_rather_than_guessing():
    with pytest.raises(UnknownFee):
        fees.fee(fees.kalshi_key("flat", 1), 0.5, 1)
    with pytest.raises(UnknownFee):
        fees.fee("somewhere:else", 0.5, 1)
    with pytest.raises(UnknownFee):
        fees.polymarket_key("astrology")


def test_a_multiplier_or_rate_that_is_not_a_finite_non_negative_number_is_refused():
    # A negative number turns the fee into a rebate and inflates EV; NaN makes
    # it nan; Infinity crashed with InvalidOperation, which paper.submit does
    # not catch. None of them is a fee rule anyone has read.
    for bad in ("kalshi:quadratic:-5", "kalshi:quadratic:NaN",
                "kalshi:quadratic:Infinity", "kalshi:quadratic:sNaN",
                "polymarket:-0.05", "polymarket:NaN", "polymarket:Infinity"):
        assert fees.well_formed(bad) is False, bad
        with pytest.raises(UnknownFee):
            fees.fee(bad, 0.5, 1)
    # Zero is a real rate: Polymarket charges nothing on geopolitics.
    assert fees.well_formed(fees.polymarket_key("geopolitics"))


def test_polymarket_takers_pay_by_category_and_makers_never():
    m = fees.polymarket_key("Sports")
    assert m == "polymarket:0.05"
    assert fees.fee(m, 0.50, 100) == pytest.approx(1.25)
    assert fees.fee(m, 0.50, 100, role="maker") == 0.0
    assert fees.fee(fees.polymarket_key("geopolitics"), 0.5, 100) == 0.0


def test_polymarket_fee_is_rounded_up_to_five_decimals():
    # docs.polymarket.com/trading/fees (read 2026-09-25): "Fees are rounded to
    # 5 decimal places." They do not say which way, so the fee is rounded up,
    # in either mode: costed slightly high, never low.
    m = fees.polymarket_key("sports")
    assert fees.fee(m, 0.01, 1) == 0.0005              # exactly 0.000495
    assert fees.fee(m, 0.37, 3) == 0.03497             # exactly 0.034965
    assert fees.fee(m, 0.37, 3, conservative=False) == 0.03497
    # The docs say a fee under 0.00001 "rounds to zero". Here it is charged as
    # 0.00001, the one place rounding up departs from their words.
    assert fees.fee(m, 0.01, 0.01) == 0.00001          # exactly 0.00000495


def test_a_book_has_no_separate_fee_and_ev_is_p_times_decimal_minus_one():
    for ml in (-150, -110, +100, +135, +400):
        dec = american_to_decimal(ml)
        assert fees.fee("book", 1 / dec, 10) == 0.0
        assert fees.ev(0.47, "book", 1 / dec) == pytest.approx(0.47 * dec - 1, abs=1e-12)


def test_ev_is_after_fees():
    # Fair 52%, buying at 50c: +4% before fees, less after them.
    before = 0.52 / 0.50 - 1
    after = fees.ev(0.52, K, 0.50, contracts=100)
    assert after < before
    assert after == pytest.approx(0.52 / (0.50 + 0.0175) - 1)


def test_nonsense_inputs_are_refused():
    for bad_price in (0, 1, -0.1, 1.2):
        with pytest.raises(ValueError):
            fees.fee(K, bad_price, 1)
    with pytest.raises(ValueError):
        fees.fee(K, 0.5, 0)
    with pytest.raises(ValueError):
        fees.fee(K, 0.5, 1, role="market")
