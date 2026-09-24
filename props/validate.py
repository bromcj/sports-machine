"""Validating a prop model BEFORE any prices exist. Free, no credits.

docs/experiments.md B1 requires this, and the ordering is the point: a prop
model has to be shown to forecast the QUANTITY well before anyone spends ten
credits a request finding out whether it beats a price. If the model cannot
beat a player's own season average, no market comparison is going to save it,
and the cheapest possible moment to learn that is now.

Three things, all of which run on outcomes alone:

  WALK-FORWARD BY MONTH   Never score a month with a model that has seen it.
                          By month rather than by season because props have far
                          more rows per unit time than games do, and because a
                          receiver's role changes inside a season in a way a
                          team's does not.

  CALIBRATION BY DECILE   Sort by predicted P(over), bucket, and compare the
                          predicted rate with the actual one. A model can have
                          a good log loss and still be systematically
                          overconfident at the extremes, which is exactly where
                          it would bet.

  LOG LOSS vs A NAIVE BASELINE   The player's own season-to-date average with a
                          Poisson on top. This is the bar. It is not a market,
                          and beating it proves nothing about edge - it only
                          proves the model has learned something beyond "this
                          guy averages four catches". Phase 5 is the standing
                          reminder of the difference between those two things.

BOOTSTRAP BY GAME, NOT BY PROP. Two receivers in the same game share a
quarterback, a game script and a defence. Resampling props independently would
treat them as independent evidence and understate every standard error here.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from props.distributions import negbin, \
    prob_over_excluding_push
from props.framework import PropSpec

# Where the naive baseline regresses to before a player has any history.
NAIVE_PRIOR_EVENTS = 3.0


def naive_baseline(rows: pd.DataFrame, spec: PropSpec,
                   player_col: str = "player_id",
                   actual_col: str = "actual",
                   date_col: str = "game_date",
                   line_col: str = "line") -> np.ndarray:
    """P(over) from the player's season-to-date mean, with a Poisson on top.

    Strictly as-of: the mean entering each game excludes that game. Implemented
    as cumsum-minus-self within (player, season) rather than a rolling mean,
    for the reason features/build_training.py records at length - a rolling
    window followed by shift can hand a player's first game of a season the
    window ending at his last game of the previous one.
    """
    d = rows.copy()
    d["_season"] = pd.to_datetime(d[date_col]).dt.year
    d = d.sort_values([player_col, date_col])
    g = d.groupby([player_col, "_season"], sort=False)
    prior_sum = g[actual_col].cumsum() - d[actual_col]
    prior_n = g.cumcount()
    league = d[actual_col].mean()
    mean = ((prior_sum + NAIVE_PRIOR_EVENTS * league)
            / (prior_n + NAIVE_PRIOR_EVENTS))

    out = np.empty(len(d))
    discrete = spec.kind == "count"
    for i, (m, line) in enumerate(zip(mean.values, d[line_col].values)):
        # Poisson is the naive choice on purpose: it is what you get if you
        # assume no overdispersion, and the real model has to beat it.
        dist = negbin(max(m, 1e-6), var_ratio=1.0 + 1e-9)
        out[i] = prob_over_excluding_push(dist, line, discrete)
    return pd.Series(out, index=d.index).reindex(rows.index).values


def outcome_over(rows: pd.DataFrame, actual_col: str = "actual",
                 line_col: str = "line") -> pd.Series:
    """1 if the actual cleared the line, 0 if not, NaN on a push.

    NaN, not 0: a push is not a loss for the over. Dropping those rows is the
    only honest thing to do when scoring a binary forecast, and doing it here
    means no caller can forget.
    """
    a, L = rows[actual_col].astype(float), rows[line_col].astype(float)
    y = (a > L).astype(float)
    return y.mask(np.isclose(a, L))


def calibration_by_decile(p: np.ndarray, y: np.ndarray,
                          n_buckets: int = 10) -> pd.DataFrame:
    d = pd.DataFrame({"p": np.asarray(p, float), "y": np.asarray(y, float)})
    d = d.dropna()
    d["bucket"] = pd.qcut(d["p"], n_buckets, duplicates="drop")
    out = (d.groupby("bucket", observed=True)
            .agg(n=("y", "size"), predicted=("p", "mean"),
                 actual=("y", "mean")).reset_index(drop=True))
    out["gap"] = out["actual"] - out["predicted"]
    return out
