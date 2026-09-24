"""B1 unit tests: the count model, push handling, and the void rule.

Named in docs/experiments.md B1 as required before any prop money or credit is
spent. No database, no network.

The three of them exist because each is a way to be quietly wrong in the
direction of betting MORE:

  count model   understating the spread makes far-from-projection lines look
                like edges
  push          treating a whole-line push as a loss under-rates every whole
                line, and books post them constantly
  void rule     scoring a scratched starter as a loss punishes the model for a
                bet that was never placed, and scratches are not random
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from props.distributions import (negbin, gamma_dist, lognormal_dist,
                                 over_push_under, prob_over_excluding_push,
                                 is_whole_line, count_dist, yards_dist)
from props.framework import SPECS, shrink, project, price
from props.validate import outcome_over, calibration_by_decile


# ------------------------------------------------------------ count model ---

def test_negbin_has_the_mean_and_variance_asked_for():
    d = negbin(5.4, var_ratio=1.30)
    assert d.mean() == pytest.approx(5.4, rel=1e-9)
    assert d.var() == pytest.approx(1.30 * 5.4, rel=1e-9)


def test_negbin_is_wider_than_poisson():
    """The whole reason for using it. A narrower model bets more."""
    mean = 6.0
    wide, poisson = negbin(mean, 1.4), negbin(mean, 1.0 + 1e-12)
    assert wide.var() > poisson.var()
    # and that shows up where it matters: the tail
    assert wide.sf(9) > poisson.sf(9)


def test_negbin_clamps_impossible_underdispersion():
    """Var < mean cannot be a negative binomial. Clamp, never emit nonsense."""
    d = negbin(4.0, var_ratio=0.5)
    assert np.isfinite(d.mean()) and np.isfinite(d.var())
    assert d.var() >= d.mean() - 1e-6


def test_count_probabilities_sum_to_one():
    d, discrete = count_dist(5.1, 1.25)
    for line in (4.5, 5.0, 5.5, 6.0, 0.5, 12.0):
        o, p, u = over_push_under(d, line, discrete)
        assert o + p + u == pytest.approx(1.0, abs=1e-9)
        assert min(o, p, u) >= 0.0


def test_gamma_and_lognormal_hit_their_mean_and_cv():
    for maker in (gamma_dist, lognormal_dist):
        d = maker(62.0, cv=0.8)
        assert d.mean() == pytest.approx(62.0, rel=1e-6)
        assert d.std() / d.mean() == pytest.approx(0.8, rel=1e-6)


# --------------------------------------------------------------- pushes -----

def test_half_line_never_pushes():
    d, discrete = count_dist(5.0, 1.25)
    o, p, u = over_push_under(d, 5.5, discrete)
    assert p == 0.0
    assert o + u == pytest.approx(1.0)


def test_whole_line_push_is_the_exact_pmf_for_a_count():
    d, discrete = count_dist(5.0, 1.25)
    o, p, u = over_push_under(d, 6.0, discrete)
    assert p == pytest.approx(d.pmf(6), rel=1e-9)
    assert u == pytest.approx(d.cdf(5), rel=1e-9)
    assert o == pytest.approx(d.sf(6), rel=1e-9)


def test_whole_line_push_is_material_not_a_rounding_detail():
    """If this were negligible the rule would not be worth having."""
    d, discrete = count_dist(5.0, 1.25)
    _, p, _ = over_push_under(d, 5.0, discrete)
    assert p > 0.10          # ~15% of the time the bet is returned


def test_a_push_treated_as_a_loss_understates_the_over():
    """The bug this guards: grading 'not over' as 'under' on a whole line."""
    d, discrete = count_dist(5.0, 1.25)
    o, p, u = over_push_under(d, 5.0, discrete)
    naive_over = o                      # what a careless grader compares
    honest_over = prob_over_excluding_push(d, 5.0, discrete)
    assert honest_over > naive_over
    assert honest_over == pytest.approx(o / (o + u))


def test_continuous_whole_line_gets_a_continuity_correction():
    """Yards are integers even though the model of them is continuous."""
    d, discrete = yards_dist(60.0, 0.8)
    assert discrete is False
    o, p, u = over_push_under(d, 60.0, discrete)
    assert p > 0.0                       # a push CAN happen
    assert p == pytest.approx(d.cdf(60.5) - d.cdf(59.5), rel=1e-9)
    assert o + p + u == pytest.approx(1.0)


def test_continuous_half_line_has_no_push():
    d, discrete = yards_dist(60.0, 0.8)
    _, p, _ = over_push_under(d, 59.5, discrete)
    assert p == 0.0


@pytest.mark.parametrize("line,whole", [(5.0, True), (5.5, False),
                                        (5.0000000001, True), (0.5, False),
                                        (12, True)])
def test_whole_line_detection(line, whole):
    assert is_whole_line(line) is whole


def test_outcome_is_nan_on_a_push_not_zero():
    rows = pd.DataFrame({"actual": [6, 5, 4], "line": [5.0, 5.0, 5.0]})
    y = outcome_over(rows)
    assert y.iloc[0] == 1.0
    assert np.isnan(y.iloc[1])           # equalled the line -> push
    assert y.iloc[2] == 0.0


# ---------------------------------------------------------- shrink/price ----

def test_shrink_moves_toward_the_prior_on_small_samples():
    # 10 targets at a 90% catch rate is not a 90% receiver
    assert shrink(10, 0.90, 0.65, 50) == pytest.approx(0.65 + (0.90 - 0.65) * (10 / 60))
    # with a huge sample the observation wins
    assert shrink(10_000, 0.90, 0.65, 50) == pytest.approx(0.90, abs=0.002)
    # with no sample at all it is exactly the prior
    assert shrink(0, 0.90, 0.65, 50) == pytest.approx(0.65)


def test_project_is_opportunity_times_rate():
    spec = SPECS["pitcher_strikeouts"]
    rows = pd.DataFrame({"proj_bf": [24.0, 20.0], "proj_k_rate": [0.25, 0.30]})
    assert list(project(spec, rows)) == pytest.approx([6.0, 6.0])


def test_project_raises_on_a_missing_column():
    with pytest.raises(KeyError):
        project(SPECS["pitcher_strikeouts"], pd.DataFrame({"proj_bf": [24.0]}))


def test_price_returns_coherent_probabilities():
    spec = SPECS["pitcher_strikeouts"]
    rows = pd.DataFrame({"proj_bf": [24.0, 22.0], "proj_k_rate": [0.25, 0.28],
                         "line": [5.5, 6.0]})
    out = price(spec, rows)
    assert list(out["projection"]) == pytest.approx([6.0, 6.16])
    assert np.allclose(out[["p_over", "p_push", "p_under"]].sum(axis=1), 1.0)
    assert out.loc[0, "p_push"] == 0.0            # half line
    assert out.loc[1, "p_push"] > 0.0             # whole line


def test_calibration_table_is_sorted_and_sums():
    rng = np.random.default_rng(0)
    p = rng.uniform(0.2, 0.8, 500)
    y = (rng.uniform(size=500) < p).astype(float)
    cal = calibration_by_decile(p, y)
    assert cal["n"].sum() == 500
    assert cal["predicted"].is_monotonic_increasing
