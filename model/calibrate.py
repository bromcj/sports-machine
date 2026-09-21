"""Calibration: fit the run-diff -> win-prob logistic k on training folds,
then check reliability by decile. Overconfident deciles = shrink k.
"""
import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar


def fit_k(pred_run_diff: np.ndarray, home_won: np.ndarray) -> float:
    """Fit the logistic steepness k by minimizing log loss on TRAIN data only."""
    y = np.asarray(home_won)

    def nll(k):
        p = np.clip(1 / (1 + np.exp(-k * pred_run_diff)), 1e-9, 1 - 1e-9)
        return -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))

    res = minimize_scalar(nll, bounds=(0.01, 2.0), method="bounded")
    return float(res.x)


def reliability_table(pred_prob: np.ndarray, home_won: np.ndarray,
                      n_buckets: int = 10) -> pd.DataFrame:
    df = pd.DataFrame({"p": pred_prob, "y": home_won})
    df["bucket"] = pd.qcut(df["p"], n_buckets, duplicates="drop")
    out = df.groupby("bucket", observed=True).agg(
        predicted=("p", "mean"), actual=("y", "mean"), n=("y", "size"))
    out["gap"] = out["actual"] - out["predicted"]
    return out.reset_index(drop=True)


def brier(pred_prob, home_won) -> float:
    return float(np.mean((np.asarray(pred_prob) - np.asarray(home_won)) ** 2))


if __name__ == "__main__":
    rng = np.random.default_rng(3)
    diff = rng.normal(0, 4, 3000)
    y = (diff + rng.normal(0, 3, 3000) > 0).astype(int)
    k = fit_k(diff, y)
    p = 1 / (1 + np.exp(-k * diff))
    print(f"fitted k = {k:.3f}   brier = {brier(p, y):.4f}")
    print(reliability_table(p, y).to_string(index=False))
