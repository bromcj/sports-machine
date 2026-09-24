"""Ridge regression on run differential with walk-forward validation.

Walk-forward: train seasons [1..k], test season k+1, roll forward.
Alpha tuned only on past folds. Compared against no-vig market baseline.
"""
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.metrics import mean_squared_error, log_loss

import sys as _sys
_sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
from config import SPORTS
from model.calibrate import fit_k

MODEL_VERSION = "ridge-margin-v2"  # one architecture, per-sport models
ALPHAS = [0.1, 1.0, 10.0, 50.0, 100.0]


def margin_to_win_prob(margin: np.ndarray, sport: str = "mlb",
                       k: float | None = None, a: float = 0.0) -> np.ndarray:
    """Logistic mapping from projected margin (home - away) to home win prob.

    k defaults per sport from config (MLB runs vs NBA points differ ~2.5x);
    refit empirically in calibrate.py per sport.

    `a` is a home intercept. Without it this returns exactly 0.500 at a
    predicted margin of zero, but home teams win ~53% of MLB games while the
    mean predicted margin is only about +0.04 runs - runs and wins are not
    linearly related near zero, and a bare sigmoid has nothing to absorb the
    difference. Measured out of sample the model under-rated home teams by
    ~2.9 points in every season, uniformly across the whole reliability curve
    (docs-calibration.md), which is what a missing intercept looks like rather
    than a wrong k.

    Defaults to 0.0, so any caller that does not pass one keeps the old
    behaviour exactly.
    """
    k = k if k is not None else SPORTS[sport]["k_default"]
    return 1.0 / (1.0 + np.exp(-(a + k * np.asarray(margin))))


def fit_intercept(margin: np.ndarray, home_won: np.ndarray, k: float) -> float:
    """Maximum-likelihood `a` for sigmoid(a + k*margin), with k held fixed.

    k fixed is the whole point: docs-calibration.md showed the miscalibration
    is a uniform shift, constant across every predicted-probability bucket. A
    wrong k would show as a gap that widens away from 0.5, and it does not.
    Fitting both would let a slope absorb an intercept's job on this sample and
    then behave differently on the next one.
    """
    from scipy.optimize import minimize_scalar
    margin, y = np.asarray(margin, dtype=float), np.asarray(home_won, dtype=float)

    def nll(a):
        p = np.clip(1.0 / (1.0 + np.exp(-(a + k * margin))), 1e-9, 1 - 1e-9)
        return -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))

    return float(minimize_scalar(nll, bounds=(-1.0, 1.0), method="bounded").x)

run_diff_to_win_prob = margin_to_win_prob  # backward-compat alias


def walk_forward(df: pd.DataFrame, feature_cols: list[str],
                 target_col: str = "run_diff", season_col: str = "season",
                 sport: str = "mlb", intercept: bool = False):
    """Expanding-window walk-forward CV. Returns per-season out-of-sample metrics.

    df must contain one row per game with features, target (home minus away runs),
    season, home_won (0/1), and novig_home_prob (market baseline) columns.

    `intercept` turns on the home intercept from docs-calibration.md. It is
    fitted leave-one-season-out INSIDE the training window - the same
    out-of-fold predictions k_fit already uses - so the intercept applied to a
    test season was never shown a game from it. Off by default: it is measured
    per sport before being switched on, and `logloss_model_noint` is always
    reported so the every-season bar stays checkable from the output rather
    than on trust.

    The reported `k_fit` is diagnostic only. It is the margin -> win-prob
    steepness the TRAINING data would choose, measured on out-of-fold
    predictions so it stays leakage-free, and it is NOT applied - predictions
    use the config k_default. Measured 2026-09: fitting k is neutral to
    slightly worse here, because these predicted margins are compressed
    (sd ~0.5 against ~4.5 for real ones) and the log-loss surface around
    k is correspondingly flat. Watch k_fit drift away from k_default as you
    add features: that is the signal the config prior has gone stale.
    """
    seasons = sorted(df[season_col].unique())
    results = []
    for i in range(2, len(seasons)):  # need >=2 train seasons
        train = df[df[season_col].isin(seasons[:i])]
        test = df[df[season_col] == seasons[i]]

        # tune alpha on the LAST train season only (never the test season)
        inner_train = df[df[season_col].isin(seasons[:i - 1])]
        inner_val = df[df[season_col] == seasons[i - 1]]
        best_alpha, best_rmse = None, np.inf
        for a in ALPHAS:
            m = make_pipeline(StandardScaler(), Ridge(alpha=a))
            m.fit(inner_train[feature_cols], inner_train[target_col])
            rmse = mean_squared_error(
                inner_val[target_col], m.predict(inner_val[feature_cols])) ** 0.5
            if rmse < best_rmse:
                best_alpha, best_rmse = a, rmse

        # Diagnostic only: what steepness would the training data pick?
        # Leave-one-season-out inside the training window, so every prediction
        # k sees was made by a model that had not seen that game.
        oof_pred, oof_y = [], []
        for s in seasons[:i]:
            fold_tr, fold_va = train[train[season_col] != s], train[train[season_col] == s]
            fm = make_pipeline(StandardScaler(), Ridge(alpha=best_alpha))
            fm.fit(fold_tr[feature_cols], fold_tr[target_col])
            oof_pred.append(fm.predict(fold_va[feature_cols]))
            oof_y.append(fold_va["home_won"].values)
        oof_pred, oof_y = np.concatenate(oof_pred), np.concatenate(oof_y)
        k_fit = fit_k(oof_pred, oof_y)
        # The home intercept, from the SAME out-of-fold predictions, so it
        # carries the same leakage guarantee k_fit does.
        a_fit = fit_intercept(oof_pred, oof_y, SPORTS[sport]["k_default"])
        a_used = a_fit if intercept else 0.0

        model = make_pipeline(StandardScaler(), Ridge(alpha=best_alpha))
        model.fit(train[feature_cols], train[target_col])
        pred_diff = model.predict(test[feature_cols])
        pred_prob = margin_to_win_prob(pred_diff, sport, a=a_used)
        prob_noint = margin_to_win_prob(pred_diff, sport)

        y = test["home_won"].values
        # Per-game paired losses, so a season's margin can be judged against
        # its own noise. Season AVERAGES cannot: two of MLB's three test
        # seasons beat the baseline by less than one standard error, which is
        # indistinguishable from luck, and a gate comparing only the means
        # could not tell. model/validation.py pools (n, mean, sd) across
        # seasons exactly, so the arrays do not need to travel.
        pm = np.clip(pred_prob, 1e-6, 1 - 1e-6)
        bm = np.clip(test["novig_home_prob"].values, 1e-6, 1 - 1e-6)
        ll_model_each = -(y * np.log(pm) + (1 - y) * np.log(1 - pm))
        ll_market_each = -(y * np.log(bm) + (1 - y) * np.log(1 - bm))
        diff = ll_market_each - ll_model_each        # positive = model better
        metrics = {
            "test_season": int(seasons[i]),
            "alpha": best_alpha,
            "k_used": SPORTS[sport]["k_default"],
            "k_fit": round(k_fit, 3),
            "a_fit": round(a_fit, 4),
            "a_used": round(a_used, 4),
            "n_games": int(len(y)),
            "ll_diff_sd": float(np.std(diff, ddof=1)) if len(diff) > 1 else 0.0,
            "rmse": mean_squared_error(test[target_col], pred_diff) ** 0.5,
            "logloss_model": log_loss(y, np.clip(pred_prob, 1e-6, 1 - 1e-6)),
            # Always reported, whether or not the intercept is applied, so the
            # "helps in EVERY season" bar can be checked from the output.
            "logloss_model_noint": log_loss(
                y, np.clip(prob_noint, 1e-6, 1 - 1e-6)),
            "brier_model": float(np.mean((pred_prob - y) ** 2)),
            "logloss_market": log_loss(
                y, np.clip(test["novig_home_prob"].values, 1e-6, 1 - 1e-6)),
            "brier_market": float(np.mean((test["novig_home_prob"].values - y) ** 2)),
            "accuracy": float(np.mean((pred_prob > 0.5) == y)),
        }
        results.append(metrics)
    return pd.DataFrame(results)


if __name__ == "__main__":
    # Smoke test on synthetic data proving the harness runs end to end.
    rng = np.random.default_rng(7)
    n = 4000
    X = rng.normal(size=(n, 5))
    true_diff = X @ np.array([0.8, 0.5, -0.4, 0.3, 0.2]) + rng.normal(0, 3.2, n)
    df = pd.DataFrame(X, columns=[f"f{i}" for i in range(5)])
    df["run_diff"] = true_diff
    df["home_won"] = (true_diff > 0).astype(int)
    df["novig_home_prob"] = margin_to_win_prob(true_diff * 0.6 + rng.normal(0, 1, n), "mlb")
    df["season"] = np.repeat(range(2022, 2027), n // 5)
    out = walk_forward(df, [f"f{i}" for i in range(5)])
    print(out.to_string(index=False))
    print("\nBar to clear: logloss_model < logloss_market on real data.")
