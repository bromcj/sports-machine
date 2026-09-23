"""shop / info / ev must separate price from forecast, exactly."""
from bets.engine import american_to_decimal


def parts(line_taken, p_fair_at_bet, p_fair_close):
    dec = american_to_decimal(line_taken)
    return (dec * p_fair_at_bet - 1,          # shop
            p_fair_close / p_fair_at_bet - 1,  # info
            dec * p_fair_close - 1)            # ev


def test_the_identity_is_exact():
    for line, pb, pc in [(+120, 0.50, 0.50), (-110, 0.55, 0.60),
                         (+250, 0.30, 0.25), (-300, 0.72, 0.80)]:
        shop, info, ev = parts(line, pb, pc)
        assert abs((1 + shop) * (1 + info) - (1 + ev)) < 1e-12


def test_pure_line_shopping_scores_nothing_on_info():
    # A better price than fair, but the fair line never moved. This is the
    # case that could otherwise pass gate 2 with no forecasting skill at all.
    shop, info, _ = parts(+120, 0.50, 0.50)
    assert shop > 0.05 and abs(info) < 1e-12


def test_a_line_moving_the_models_way_scores_on_info():
    _, info, _ = parts(+100, 0.50, 0.58)
    assert info > 0.1


def test_a_line_moving_against_the_model_scores_negative():
    _, info, _ = parts(+100, 0.50, 0.42)
    assert info < -0.1
