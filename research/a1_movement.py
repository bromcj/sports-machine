"""A1 follow-up: is the movement slope real, and is it worth anything?

    python research/a1_movement.py

A1 predicted the movement slope would be zero and it was not: regressing the
market's morning->close move on our disagreement with the morning price gives
+0.0284, t = +7.6, positive in all three seasons. Under the pre-registered rule
that is a pass on significance and on consistency.

Before it gets reported as a finding it has to survive three things. The first
two are artifacts that would produce exactly this result out of nothing; the
third is the pre-registered "bettable" clause that a pass still has to clear.

These are DIAGNOSTICS ON A POSITIVE, run after the fact and labelled as such.
They can only take a pass away, never create one - which is why running them
after seeing the result is legitimate where re-tuning a failed test would not
be.

----------------------------------------------------------------------------
1. THE SHARED-DENOMINATOR ARTIFACT (the serious one)

    disagree = logit(p_model)  - logit(p_morning)
    moved    = logit(p_close)  - logit(p_morning)

`logit(p_morning)` appears in BOTH, with a minus sign in both. So any error in
measuring the morning price - and it is one snapshot, from one book, de-vigged
- pushes both quantities the same way and manufactures a positive slope with no
information in it at all. The size of the fake slope is Var(morning noise) /
Var(disagree), and measured book-to-book disagreement at 10:00 ET (sd 0.023
logit) is enough to account for a fifth of what we saw.

The fix is to measure the morning price TWICE from independent sources -
Pinnacle (M1) and the de-vigged consensus of DraftKings, FanDuel and BetMGM
(M2) - and never let the same measurement into both sides of a covariance.

Simply swapping one for the other is NOT enough, and the first version of this
file got that wrong. Writing M1 = T + e1 and M2 = T + e2 for the unobserved
true morning price T, regressing (close - M1) on (model - M2) leaves +e1 in the
numerator paired with nothing, but regressing (close - M2) on (model - M1)
leaves -e1 in Y and -e1 in X with OPPOSITE signs, which biases that version
DOWNWARD by as much as the naive version is biased upward. Two wrong answers
that straddle the right one.

The estimator that is actually unbiased uses both measurements in both places:

    slope = Cov(close - M1, model - M2) / Cov(model - M1, model - M2)

The numerator pairs e1 with e2, which are independent, so it vanishes. The
denominator is a covariance rather than a variance for the same reason, and
recovers Var(model - T) without the +Var(e1) that inflates the naive one. Both
corrections push in the same direction: the naive slope is too big.

----------------------------------------------------------------------------
2. THE LATE-STARTER CONFOUND

The model is built on the pitcher who ACTUALLY started. The 10:00 ET price
sometimes is not - probables get scratched, and the close always knows. A model
that knows the real starter will "predict" the market's move toward the real
starter without knowing anything the market could not have known at 10:00, and
would be unbettable: we would not have known either.

Tested by refitting the whole walk-forward with the two starter features
dropped. Bullpen form, offense, rest and park are all public at 10:00 and none
of them changes on a scratch. If the slope survives without the starter
features, this confound is not what is driving it.

----------------------------------------------------------------------------
3. BETTABLE (pre-registered)

A slope is CLV, not profit. The pre-registered clause is that the implied
morning-price EV has to clear the vig at a NJ book. Both halves are computed:
what the move is worth if we could trade it at no cost, and what is left after
paying DraftKings/FanDuel/BetMGM their overround on the morning price.
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
from research.a1_moneyline import build, FEATURES, _fit_intercept
from research.stats import logit, expit, SEED

SOFT_MORNING = ["p_draftkings_morning", "p_fanduel_morning", "p_betmgm_morning"]
STARTER_FEATURES = ["home_sp_kbb_5s", "away_sp_kbb_5s"]


def _slope_ci(x, y, days, n_boot=2000, seed=SEED):
    ok = np.isfinite(x) & np.isfinite(y)
    x, y, days = np.asarray(x)[ok], np.asarray(y)[ok], np.asarray(days)[ok]
    point = np.polyfit(x, y, 1)[0]
    uniq = np.unique(days)
    idx = {u: np.where(days == u)[0] for u in uniq}
    rng = np.random.default_rng(seed)
    boot = np.empty(n_boot)
    for i in range(n_boot):
        pick = rng.integers(0, len(uniq), len(uniq))
        ii = np.concatenate([idx[uniq[j]] for j in pick])
        boot[i] = np.polyfit(x[ii], y[ii], 1)[0]
    se = boot.std(ddof=1)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return dict(slope=point, se=se, lo=lo, hi=hi, t=point / se, n=len(x))


def _line(label, r):
    print(f"  {label:44s} {r['slope']:+.5f}  "
          f"[{r['lo']:+.5f},{r['hi']:+.5f}]  t={r['t']:+5.2f}  n={r['n']:,}")


def _ev_slope(close, model, m1, m2):
    """Cov(close-M1, model-M2) / Cov(model-M1, model-M2). See the docstring."""
    num = np.cov(close - m1, model - m2)[0, 1]
    den = np.cov(model - m1, model - m2)[0, 1]
    return num / den


def _ev_slope_ci(d, model_col, n_boot=2000, seed=SEED):
    close = d["l_fair_close"].values
    model = d[model_col].values
    m1, m2 = d["l_morn_pin"].values, d["l_morn_soft"].values
    days = d["day"].values
    uniq = np.unique(days)
    idx = {u: np.where(days == u)[0] for u in uniq}
    rng = np.random.default_rng(seed)
    boot = np.empty(n_boot)
    for i in range(n_boot):
        pick = rng.integers(0, len(uniq), len(uniq))
        ii = np.concatenate([idx[uniq[j]] for j in pick])
        boot[i] = _ev_slope(close[ii], model[ii], m1[ii], m2[ii])
    point = _ev_slope(close, model, m1, m2)
    se = boot.std(ddof=1)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return dict(slope=point, se=se, lo=lo, hi=hi, t=point / se, n=len(d))


def split_sample(d: pd.DataFrame):
    """Measure the morning price from independent sources on each side."""
    print("=" * 78)
    print("1. SHARED-DENOMINATOR ARTIFACT")
    print("=" * 78)
    d = d.copy()
    d["l_morn_pin"] = logit(d["p_pinnacle_morning"])
    d["l_morn_soft"] = logit(d[SOFT_MORNING].mean(axis=1))
    d = d[d["l_morn_pin"].notna() & d["l_morn_soft"].notna()].copy()
    print(f"\ngames with BOTH a Pinnacle morning price and all three NJ "
          f"morning prices: {len(d):,}")

    print("\nnaive - the same morning price on both sides (biased UP):")
    _line("pinnacle morning both sides",
          _slope_ci(d["l_model"] - d["l_morn_pin"],
                    d["l_fair_close"] - d["l_morn_pin"], d["day"]))
    _line("soft-consensus morning both sides",
          _slope_ci(d["l_model"] - d["l_morn_soft"],
                    d["l_fair_close"] - d["l_morn_soft"], d["day"]))

    print("\nnaive swap - looks like a fix, is biased DOWN (see docstring):")
    _line("disagree vs PINNACLE, move from SOFT",
          _slope_ci(d["l_model"] - d["l_morn_pin"],
                    d["l_fair_close"] - d["l_morn_soft"], d["day"]))

    print("\nunbiased two-measurement estimator:")
    r = _ev_slope_ci(d, "l_model")
    _line("corrected", r)
    return d, r


def no_starter_model(sport="mlb") -> pd.DataFrame:
    """Refit the walk-forward with the starter features dropped."""
    feats = [f for f in FEATURES if f not in STARTER_FEATURES]
    df = pd.read_parquet(paths.training_table(sport))
    df = df.dropna(subset=FEATURES + ["run_diff", "home_won"])
    k = SPORTS[sport]["k_default"]
    seasons = sorted(df["season"].unique())
    rows = []
    for i in range(2, len(seasons)):
        train, test = (df[df["season"].isin(seasons[:i])],
                       df[df["season"] == seasons[i]])
        inner_tr = df[df["season"].isin(seasons[:i - 1])]
        inner_va = df[df["season"] == seasons[i - 1]]
        best_a, best_rmse = None, np.inf
        for a in ALPHAS:
            m = make_pipeline(StandardScaler(), Ridge(alpha=a))
            m.fit(inner_tr[feats], inner_tr["run_diff"])
            r = mean_squared_error(inner_va["run_diff"],
                                   m.predict(inner_va[feats])) ** 0.5
            if r < best_rmse:
                best_a, best_rmse = a, r
        oof_m, oof_y = [], []
        for s in seasons[:i]:
            tr, va = train[train["season"] != s], train[train["season"] == s]
            fm = make_pipeline(StandardScaler(), Ridge(alpha=best_a))
            fm.fit(tr[feats], tr["run_diff"])
            oof_m.append(fm.predict(va[feats]))
            oof_y.append(va["home_won"].values)
        a0 = _fit_intercept(np.concatenate(oof_m), np.concatenate(oof_y), k)
        model = make_pipeline(StandardScaler(), Ridge(alpha=best_a))
        model.fit(train[feats], train["run_diff"])
        rows.append(pd.DataFrame({
            "game_id": test["game_id"].values,
            "p_nostart": expit(a0 + k * model.predict(test[feats])),
        }))
    return pd.concat(rows, ignore_index=True)


def starter_confound(d: pd.DataFrame):
    print("\n" + "=" * 78)
    print("2. LATE-STARTER CONFOUND")
    print("=" * 78)
    d = d.merge(no_starter_model(), on="game_id", how="inner")
    d["l_nostart"] = logit(d["p_nostart"])
    print(f"\nrefit without {STARTER_FEATURES}, n={len(d):,}")
    print("corrected estimator throughout, so this is on top of fix 1:")
    full = _ev_slope_ci(d, "l_model")
    nost = _ev_slope_ci(d, "l_nostart")
    _line("full model (has starter features)", full)
    _line("starter features removed", nost)

    # Guard against the obvious misreading: a smaller slope would also appear
    # if dropping two features left the model with nothing to disagree about.
    # It does not - without them it disagrees MORE, and predicts the move LESS.
    print(f"\n  sd of disagreement, full model        "
          f"{np.std(d['l_model'] - d['l_morn_pin']):.4f}")
    print(f"  sd of disagreement, no starter feats   "
          f"{np.std(d['l_nostart'] - d['l_morn_pin']):.4f}")
    print("  (if the second is not smaller, the drop is not a power problem)")

    # A late scratch is a big, rare event. If that is the mechanism, trimming
    # the games where the market moved most should gut the slope. If the market
    # is simply slow on starters generally, trimming should barely touch it.
    print("\n  concentration: is it a few games where the market moved a lot?")
    moved_abs = (d["l_fair_close"] - d["l_morn_pin"]).abs()
    for q in (1.00, 0.99, 0.98, 0.95, 0.90):
        keep = d[moved_abs <= moved_abs.quantile(q)]
        r = _ev_slope_ci(keep, "l_model", n_boot=600)
        print(f"    trim to |move| <= p{int(q * 100):<3d}  n={len(keep):>5,}  "
              f"slope {r['slope']:+.5f}  t={r['t']:+5.2f}")
    return nost


def bettable(d: pd.DataFrame):
    """The pre-registered clause: does any of this clear the vig?"""
    print("\n" + "=" * 78)
    print("3. BETTABLE? (pre-registered clause)")
    print("=" * 78)
    d = d.dropna(subset=SOFT_MORNING + ["p_pinnacle_morning"]).copy()
    d["l_morn_pin"] = logit(d["p_pinnacle_morning"])
    d["l_morn_soft"] = logit(d[SOFT_MORNING].mean(axis=1))
    d["dis"] = d["l_model"] - d["l_morn_pin"]

    # honest slope, then what a 1-sd disagreement is worth in probability
    r = _ev_slope_ci(d, "l_model")
    sd = d["dis"].std()
    move_logit = r["slope"] * sd
    print(f"\ndisagreement sd on the logit scale: {sd:.4f}")
    print(f"honest slope: {r['slope']:+.5f}")
    print(f"=> a one-sd disagreement predicts the close moving "
          f"{move_logit:+.5f} logit toward us")
    print(f"   near a coin flip that is {100 * move_logit * 0.25:+.3f} "
          f"percentage points of probability.")

    print("\nagainst what it costs to trade:")
    for b in ("draftkings", "fanduel", "betmgm"):
        v = d[f"vig_{b}_morning"].mean()
        print(f"  {b:11s} mean morning overround {100 * v:.2f}%  -> a bet must "
              f"be worth {100 * v / 2:.2f} pts just to break even")

    print("\nDIRECT TEST - bet the disagreement at the NJ morning price, top "
          "decile only:")
    print("(the strongest version of the strategy this slope would imply)")
    cut = d["dis"].abs().quantile(0.90)
    top = d[d["dis"].abs() >= cut].copy()
    side_home = top["dis"] > 0
    # de-vigged best NJ morning price on the side we would take
    p_soft = top[SOFT_MORNING]
    p_home_best = p_soft.min(axis=1)        # cheapest home price = best for us
    p_away_best = (1 - p_soft).min(axis=1)
    p_taken = np.where(side_home, p_home_best, p_away_best)
    won = np.where(side_home, top["home_won"], 1 - top["home_won"])
    # decimal odds implied by the de-vigged price, then the book's vig added
    # back on: what we would actually be paid
    vig = top[["vig_draftkings_morning", "vig_fanduel_morning",
               "vig_betmgm_morning"]].min(axis=1).values
    dec_fair = 1.0 / p_taken
    dec_real = dec_fair / (1.0 + vig)
    roi = won * (dec_real - 1) - (1 - won)
    clv = np.where(side_home,
                   top["l_fair_close"] - top["l_morn_soft"],
                   -(top["l_fair_close"] - top["l_morn_soft"]))
    print(f"\n  bets: {len(top):,}  (|disagreement| >= {cut:.3f} logit)")
    print(f"  CLV, our side, logit:  mean {clv.mean():+.5f}  "
          f"(positive = the close moved to us)")
    print(f"  win rate {100 * won.mean():.2f}%   "
          f"break-even {100 * (1 / dec_real).mean():.2f}%")
    print(f"  ROI at the NJ morning price, vig paid: "
          f"{100 * roi.mean():+.2f}% per unit  (sd of the mean "
          f"{100 * roi.std() / np.sqrt(len(roi)):.2f})")
    return roi, clv


if __name__ == "__main__":
    panel = build()
    clean, _ = split_sample(panel)
    starter_confound(clean)
    bettable(panel)
