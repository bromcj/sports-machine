"""Turning a projection into P(over the posted line). Sport-agnostic. Free.

Every prop in docs/experiments.md B1 is modelled the same way - opportunity x
rate - and every one of them ends here, because a book does not pay out on a
projection. It pays out on whether the number cleared a line. A projection of
5.4 strikeouts says nothing about a 5.5 line until you say how spread out the
5.4 is.

WHY NOT POISSON. Poisson forces variance = mean. Real counts are wider than
that: a pitcher's strikeout total varies with how long he lasts as well as how
often he misses bats, and those compound. Using Poisson understates the spread,
which systematically overstates confidence on lines far from the projection -
exactly where a model would most want to bet, and exactly where it would be
most wrong. The negative binomial adds one parameter for that.

PUSH IS NOT A ROUNDING DETAIL. Books post whole-number prop lines
constantly - "over 5.5" and "over 6" are both normal - and on a whole line a
result equal to the line is a PUSH: the stake comes back. A model that treats
6 as "not over" and grades it a loss will systematically under-rate every
whole line it sees. So every function here returns three probabilities that sum
to one, and the caller is forced to acknowledge the middle one.

CONTINUITY FOR YARDS. Receiving yards are integers, but the sensible
distribution for them is continuous (gamma or lognormal). A continuous
distribution puts exactly zero probability on any single value, which would say
a push can never happen. It can. So a whole line on a continuous quantity gets
a continuity correction: push is the mass between L - 0.5 and L + 0.5.
"""
import numpy as np
from scipy import stats

# A line is treated as whole when it is within this of an integer. Odds feeds
# send 5.0 and 5 and occasionally 4.999999; none of those is a half line.
WHOLE_TOL = 1e-6


def is_whole_line(line: float) -> bool:
    return abs(line - round(line)) < WHOLE_TOL


def negbin(mean: float, var_ratio: float = 1.25):
    """Negative binomial with this mean and Var = var_ratio * mean.

    var_ratio is the overdispersion: 1.0 is Poisson, and anything below it is
    impossible for a negative binomial, so it is clamped rather than allowed to
    produce a silently nonsensical r.
    """
    mean = max(float(mean), 1e-9)
    var_ratio = max(float(var_ratio), 1.0 + 1e-9)
    var = var_ratio * mean
    # scipy's nbinom(n, p): mean = n(1-p)/p, var = n(1-p)/p^2  =>  p = mean/var
    p = mean / var
    n = mean * p / (1.0 - p)
    return stats.nbinom(n, p)


def gamma_dist(mean: float, cv: float = 0.75):
    """Gamma with this mean and coefficient of variation.

    Gamma rather than normal because yards are non-negative and right-skewed -
    a receiver's ceiling is much further from his median than his floor is.
    """
    mean = max(float(mean), 1e-9)
    cv = max(float(cv), 1e-6)
    shape = 1.0 / (cv ** 2)
    return stats.gamma(a=shape, scale=mean / shape)


def lognormal_dist(mean: float, cv: float = 0.75):
    """Lognormal with this mean and coefficient of variation."""
    mean = max(float(mean), 1e-9)
    cv = max(float(cv), 1e-6)
    sigma = np.sqrt(np.log(1.0 + cv ** 2))
    mu = np.log(mean) - 0.5 * sigma ** 2
    return stats.lognorm(s=sigma, scale=np.exp(mu))


def over_push_under(dist, line: float, discrete: bool) -> tuple:
    """(P(over), P(push), P(under)). Always sums to 1.

    On a HALF line there is no push and the three collapse to two.
    On a WHOLE line:
      discrete   push = P(X == line) exactly
      continuous push = P(line - 0.5 < X < line + 0.5), the continuity
                 correction, because the underlying quantity is reported as an
                 integer even though the model of it is continuous
    """
    line = float(line)
    if not is_whole_line(line):
        if discrete:
            # over means strictly greater; for a half line, X >= ceil(line)
            under = dist.cdf(np.floor(line))
        else:
            under = dist.cdf(line)
        return 1.0 - under, 0.0, under

    L = round(line)
    if discrete:
        push = dist.pmf(L)
        under = dist.cdf(L - 1)
    else:
        push = dist.cdf(L + 0.5) - dist.cdf(L - 0.5)
        under = dist.cdf(L - 0.5)
    over = 1.0 - under - push
    # Numerical hygiene: tiny negatives happen in the far tail and would turn
    # into a negative probability in a log loss.
    over, push, under = (max(x, 0.0) for x in (over, push, under))
    total = over + push + under
    return over / total, push / total, under / total


def prob_over_excluding_push(dist, line: float, discrete: bool) -> float:
    """P(over | not a push). The number to compare with a book's over price.

    A book's over/under prices on a whole line are prices on the two NON-push
    outcomes - a push returns the stake and is not a loss for either side. So
    the probability that belongs beside the posted price is conditional on the
    bet resolving at all. Comparing an unconditional P(over) with a book's
    de-vigged over price silently under-rates every whole line in the sample.
    """
    over, push, under = over_push_under(dist, line, discrete)
    live = over + under
    if live <= 0:
        return 0.5
    return over / live


def count_dist(mean: float, var_ratio: float = 1.25):
    """The distribution for a COUNT prop (strikeouts, receptions, carries)."""
    return negbin(mean, var_ratio), True


def yards_dist(mean: float, cv: float = 0.75, family: str = "gamma"):
    """The distribution for a YARDS prop."""
    d = gamma_dist(mean, cv) if family == "gamma" else lognormal_dist(mean, cv)
    return d, False
