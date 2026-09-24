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
from props.distributions import negbin, over_push_under, \
    prob_over_excluding_push
from props.framework import PropSpec, price, drop_voids
from research.stats import logloss, block_bootstrap, fmt

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


def walk_forward_months(rows: pd.DataFrame, fit_predict,
                        date_col: str = "game_date",
                        min_train_months: int = 2) -> pd.DataFrame:
    """Score each month with a model fitted only on earlier months.

    `fit_predict(train_rows, test_rows) -> array of P(over) for test_rows`.
    Kept as a callback so this harness never has to know what the model is.
    """
    d = rows.copy()
    d["_month"] = pd.to_datetime(d[date_col]).dt.to_period("M")
    months = sorted(d["_month"].unique())
    out = []
    for i in range(min_train_months, len(months)):
        train = d[d["_month"].isin(months[:i])]
        test = d[d["_month"] == months[i]].copy()
        if len(test) == 0:
            continue
        test["p_model"] = fit_predict(train, test)
        out.append(test)
    if not out:
        raise ValueError(
            f"only {len(months)} month(s) of data; need more than "
            f"{min_train_months} to walk forward")
    return pd.concat(out)


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


def report(scored: pd.DataFrame, spec: PropSpec,
           model_col: str = "p_model", naive_col: str = "p_naive",
           game_col: str = "game_id") -> dict:
    """The full B1 verdict for one prop. Returns the numbers as well."""
    y = outcome_over(scored)
    live = y.notna()
    n_push = int((~live).sum())
    s = scored[live].copy()
    y = y[live].values

    ll_model = logloss(s[model_col].values, y)
    ll_naive = logloss(s[naive_col].values, y)
    diff = ll_naive - ll_model          # positive = model better

    print("=" * 74)
    print(f"B1 validation - {spec.key}")
    print("=" * 74)
    print(f"\n{len(scored):,} props scored, {n_push} pushes dropped, "
          f"{len(s):,} live")
    print(f"{s[game_col].nunique():,} distinct games "
          f"(the bootstrap unit - two props in one game are not two bets)")

    print(f"\n  log loss, model  {ll_model.mean():.6f}")
    print(f"  log loss, naive  {ll_naive.mean():.6f}   "
          f"(season-to-date mean + Poisson)")
    r = block_bootstrap(diff, s[game_col].values, n_boot=4000)
    print("  improvement      " + fmt(r, places=6))
    print("\n  Positive means the model beats a player's own season average.")
    print("  That is the MINIMUM bar and says nothing about beating a price.")

    print("\ncalibration by decile of predicted P(over)")
    cal = calibration_by_decile(s[model_col].values, y)
    print(f"  {'n':>6s} {'predicted':>10s} {'actual':>9s} {'gap':>8s}")
    for row in cal.itertuples():
        print(f"  {row.n:>6,} {row.predicted:>10.4f} {row.actual:>9.4f} "
              f"{row.gap:>+8.4f}")
    worst = cal["gap"].abs().max()
    print(f"\n  worst decile gap {worst:.4f}. A model that is fine on average")
    print("  and wrong at the extremes will bet exactly where it is wrong.")
    return {"n": len(s), "n_push": n_push, "ll_model": float(ll_model.mean()),
            "ll_naive": float(ll_naive.mean()), "improvement": r,
            "worst_decile_gap": float(worst)}
