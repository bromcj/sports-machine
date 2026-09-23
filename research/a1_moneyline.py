"""A1: does the MLB model carry ANY information the closing price lacks?

    python research/a1_moneyline.py

Pre-registered in docs/experiments.md. Phase 5 already established that the
model loses to the close outright (-0.0099, t = -5.5). That is a statement
about the model as a forecaster. It is not the same statement as "the model
knows nothing the price does not", because a model can be badly calibrated,
badly scaled, or right about one small thing and wrong about everything else,
and still lose on total log loss.

So this asks the narrower question. Anchor on the market and let the model
contribute only the part where it DISAGREES:

    logit(p) = logit(p_morning) + b0 + b1 * (logit(p_model) - logit(p_morning))

`logit(p_morning)` enters as a fixed offset - coefficient pinned at 1, not
estimated. That is what makes b1 interpretable: it is the weight the data wants
to put on our disagreement with the price, given the price. b1 = 0 means the
disagreement is noise. b1 < 0 means the market is RIGHT when we disagree, i.e.
our disagreement is worse than useless and should be faded.

PREDICTED BEFORE FITTING (docs/experiments.md): b1 = 0, or negative.

THE MOVEMENT TEST is the one that could change what we do. Regress the market's
own move on our disagreement:

    logit(p_close) - logit(p_morning)  ~  logit(p_model) - logit(p_morning)

A positive slope means the market later moves TOWARD where we already were -
an edge available in the morning that is gone by the close. Predicted 0.

HONEST NAMING. "morning" is the 10:00 ET game-day snapshot, median ~8.8 h
before first pitch. It is not a true opener. See market_panel.py.

LEAKAGE. Model probabilities are strictly out-of-sample: season k is predicted
by a model fit on seasons before k, exactly as model/train.py:walk_forward
does it, and the home intercept from docs-calibration.md is fitted
leave-one-season-out INSIDE the training window, so nothing that touched a
game's own season ever reaches that game's prediction.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_squared_error

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import paths
from config import SPORTS
from model.train import ALPHAS
from research.market_panel import game_panel
from research.stats import (logit, expit, logloss, block_bootstrap,
                            leave_one_out, fmt)

FEATURES = ["home_pen_kbb_30d", "home_pen_pitches_3d", "home_off_woba_30d",
            "home_sp_kbb_5s", "away_pen_kbb_30d", "away_pen_pitches_3d",
            "away_off_woba_30d", "away_sp_kbb_5s", "home_rest_days",
            "away_rest_days", "park_factor"]


def _fit_intercept(margin, y, k):
    """The docs-calibration.md home intercept: p = sigmoid(a + k*margin).

    Fitted by one-dimensional logistic regression with the slope pinned at k,
    which is what "an intercept, not a slope" means operationally.
    """
    from scipy.optimize import minimize_scalar

    def nll(a):
        p = np.clip(expit(a + k * margin), 1e-9, 1 - 1e-9)
        return -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))

    return float(minimize_scalar(nll, bounds=(-1.0, 1.0), method="bounded").x)


def model_predictions(sport="mlb") -> pd.DataFrame:
    """Out-of-sample p_model per game, walk-forward, with the home intercept."""
    df = pd.read_parquet(paths.training_table(sport))
    df = df.dropna(subset=FEATURES + ["run_diff", "home_won"])
    k = SPORTS[sport]["k_default"]
    seasons = sorted(df["season"].unique())

    rows = []
    for i in range(2, len(seasons)):
        train = df[df["season"].isin(seasons[:i])]
        test = df[df["season"] == seasons[i]]

        # alpha on the last training season only - never the test season
        inner_tr = df[df["season"].isin(seasons[:i - 1])]
        inner_va = df[df["season"] == seasons[i - 1]]
        best_a, best_rmse = None, np.inf
        for a in ALPHAS:
            m = make_pipeline(StandardScaler(), Ridge(alpha=a))
            m.fit(inner_tr[FEATURES], inner_tr["run_diff"])
            r = mean_squared_error(inner_va["run_diff"],
                                   m.predict(inner_va[FEATURES])) ** 0.5
            if r < best_rmse:
                best_a, best_rmse = a, r

        # home intercept, leave-one-season-out inside the training window, so
        # every margin it sees came from a model that had not seen that game
        oof_margin, oof_y = [], []
        for s in seasons[:i]:
            tr, va = train[train["season"] != s], train[train["season"] == s]
            fm = make_pipeline(StandardScaler(), Ridge(alpha=best_a))
            fm.fit(tr[FEATURES], tr["run_diff"])
            oof_margin.append(fm.predict(va[FEATURES]))
            oof_y.append(va["home_won"].values)
        intercept = _fit_intercept(np.concatenate(oof_margin),
                                   np.concatenate(oof_y), k)

        model = make_pipeline(StandardScaler(), Ridge(alpha=best_a))
        model.fit(train[FEATURES], train["run_diff"])
        margin = model.predict(test[FEATURES])
        rows.append(pd.DataFrame({
            "game_id": test["game_id"].values,
            "season": test["season"].values,
            "p_model_raw": expit(k * margin),
            "p_model": expit(intercept + k * margin),
            "margin": margin,
            "alpha": best_a,
            "intercept": intercept,
        }))
    return pd.concat(rows, ignore_index=True)


def build() -> pd.DataFrame:
    p = game_panel()
    m = model_predictions()
    d = p.merge(m, on=["game_id", "season"], how="inner")
    d = d.dropna(subset=["p_fair_close", "p_fair_morning", "p_model"])
    d["day"] = pd.to_datetime(d["game_date"]).dt.strftime("%Y-%m-%d")
    for c in ("p_fair_close", "p_fair_morning", "p_model", "p_model_raw"):
        d["l_" + c.replace("p_", "")] = logit(d[c])
    d["disagree"] = d["l_model"] - d["l_fair_morning"]
    d["moved"] = d["l_fair_close"] - d["l_fair_morning"]
    return d


def _walk_forward_anchor(d: pd.DataFrame) -> pd.DataFrame:
    """Fit the anchored model on earlier seasons, score the later one.

    b0 and b1 are two numbers fit on thousands of games, so in-sample fitting
    would barely flatter them - but "barely" is not "not at all", and the whole
    point of this exercise is that the previous plan died of results that were
    not as out-of-sample as they looked.
    """
    seasons = sorted(d["season"].unique())
    out = []
    for i in range(1, len(seasons)):
        tr = d[d["season"].isin(seasons[:i])]
        te = d[d["season"] == seasons[i]].copy()
        b0, b1 = _fit_offset_logit(tr["disagree"].values,
                                   tr["l_fair_morning"].values,
                                   tr["home_won"].values)
        te["b0"], te["b1"] = b0, b1
        te["p_anchor"] = expit(te["l_fair_morning"] + b0 + b1 * te["disagree"])
        out.append(te)
    return pd.concat(out, ignore_index=True)


def _fit_offset_logit(x, offset, y):
    """Maximum likelihood for logit(p) = offset + b0 + b1*x. Offset fixed."""
    from scipy.optimize import minimize

    def nll(b):
        p = np.clip(expit(offset + b[0] + b[1] * x), 1e-12, 1 - 1e-12)
        return -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))

    r = minimize(nll, np.array([0.0, 0.0]), method="BFGS")
    return float(r.x[0]), float(r.x[1])


def report():
    d = build()
    print("=" * 74)
    print("A1  MLB moneyline, market-anchored")
    print("=" * 74)
    print(f"\ngames with a morning price, a close and an out-of-sample "
          f"prediction: {len(d):,}")
    print(d.groupby("season").size().to_string())
    print("\nhome intercept fitted per test season (docs-calibration.md):")
    print(d.groupby("season")["intercept"].first().round(4).to_string())

    # ---------------------------------------------------------------- b1 ----
    print("\n" + "-" * 74)
    print("b1: the weight the data wants on our disagreement with the price")
    print("predicted before fitting: b1 = 0, or negative")
    print("-" * 74)
    b0, b1 = _fit_offset_logit(d["disagree"].values,
                               d["l_fair_morning"].values,
                               d["home_won"].values)
    print(f"\npooled, all seasons:   b0 = {b0:+.4f}   b1 = {b1:+.4f}")

    # CI for b1 by the same day-block bootstrap the rest of the file uses
    days = d["day"].values
    uniq = np.unique(days)
    rng = np.random.default_rng(20260923)
    boot = []
    idx_by_day = {u: np.where(days == u)[0] for u in uniq}
    for _ in range(1000):
        pick = rng.integers(0, len(uniq), len(uniq))
        ii = np.concatenate([idx_by_day[uniq[j]] for j in pick])
        boot.append(_fit_offset_logit(d["disagree"].values[ii],
                                      d["l_fair_morning"].values[ii],
                                      d["home_won"].values[ii])[1])
    boot = np.array(boot)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    print(f"  95% CI [{lo:+.4f}, {hi:+.4f}]   se {boot.std(ddof=1):.4f}   "
          f"t {b1 / boot.std(ddof=1):+.2f}")
    print("\n  per season, fit on that season alone (descriptive):")
    for s, g in d.groupby("season"):
        _, sb1 = _fit_offset_logit(g["disagree"].values,
                                   g["l_fair_morning"].values,
                                   g["home_won"].values)
        print(f"    {s}  b1 = {sb1:+.4f}")

    # ------------------------------------------------------- log loss ----
    print("\n" + "-" * 74)
    print("log loss: anchored model vs the morning price it is anchored to")
    print("walk-forward: b0/b1 fit on earlier seasons only")
    print("-" * 74)
    w = _walk_forward_anchor(d)
    ll_anchor = logloss(w["p_anchor"], w["home_won"])
    ll_morning = logloss(w["p_fair_morning"], w["home_won"])
    diff = ll_morning - ll_anchor           # positive = anchored model better
    print(f"\n{'season':>7s} {'n':>6s} {'anchored':>10s} {'morning':>10s} "
          f"{'margin':>10s}")
    for s, g in w.groupby("season"):
        a = logloss(g["p_anchor"], g["home_won"]).mean()
        m = logloss(g["p_fair_morning"], g["home_won"]).mean()
        print(f"{s:>7d} {len(g):>6,} {a:>10.6f} {m:>10.6f} {m - a:>+10.6f}")
    r = block_bootstrap(diff, w["day"].values)
    print(f"\npooled margin (positive = the model adds something):")
    print("  " + fmt(r, places=6))
    print("\nleave-one-season-out:")
    for s, rr in leave_one_out(diff, w["day"].values, w["season"].values).items():
        print(f"  drop {s}: " + fmt(rr, places=6))

    # -------------------------------------------------------- movement ----
    print("\n" + "-" * 74)
    print("movement test: does the market later move TOWARD our disagreement?")
    print("predicted before fitting: slope = 0")
    print("-" * 74)
    x, ymov = d["disagree"].values, d["moved"].values
    slope = np.polyfit(x, ymov, 1)
    boot_s = []
    for _ in range(2000):
        pick = rng.integers(0, len(uniq), len(uniq))
        ii = np.concatenate([idx_by_day[uniq[j]] for j in pick])
        boot_s.append(np.polyfit(x[ii], ymov[ii], 1)[0])
    boot_s = np.array(boot_s)
    lo, hi = np.percentile(boot_s, [2.5, 97.5])
    print(f"\npooled slope {slope[0]:+.5f}  95% CI [{lo:+.5f}, {hi:+.5f}]  "
          f"t {slope[0] / boot_s.std(ddof=1):+.2f}")
    print("\n  per season:")
    for s, g in d.groupby("season"):
        sl = np.polyfit(g["disagree"], g["moved"], 1)[0]
        print(f"    {s}  slope = {sl:+.5f}   n={len(g):,}")
    print(f"\n  for scale: the market's own move has sd "
          f"{d['moved'].std():.4f} on the logit scale, and our disagreement "
          f"has sd {d['disagree'].std():.4f}.")
    return d, w


if __name__ == "__main__":
    report()
