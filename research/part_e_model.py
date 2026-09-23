"""E1, E2, E3, E6: is the market's recipe wrong? Free.

    python research/part_e_model.py

All four are pre-registered in docs/experiments.md, with directions written
before anything was fit. They share one frame - the market panel joined to the
proper starter and bullpen measures from research/pitching.py - so they are run
together.

E1 IS THE CENTRAL ONE. "The market overvalues starting pitchers and undervalues
bullpens" is the single most-repeated claim about modern baseball betting, and
it is testable directly: fit the same features twice, once to explain the
CLOSING PRICE and once to explain the OUTCOME, and compare the coefficients.
Where the market's weight is bigger than reality's, the market is overvaluing
that thing.

    (1)  logit(p_close) ~ features        the market's recipe
    (2)  outcome        ~ features        reality's recipe
    (3)  outcome        ~ offset(logit p_close) + features

(3) is the decisive one and (1)-(2) are descriptive. Comparing (1) with (2)
directly is slightly unfair to the market - E[logit p | X] is not
logit(E[y | X]), so the two projections need not agree exactly even for a
perfectly calibrated market. (3) has no such problem: it asks whether a feature
predicts the outcome ON TOP OF the price, which is a clean question with a
clean null of zero. Every "is it bettable" claim below is read off (3).

WHY THE MEASURES HAD TO BE REBUILT FIRST. The model's own starter feature is
K-BB% over five starts, which is more noise than signal, and a badly measured
regressor produces an attenuated coefficient - which is indistinguishable from
"the market underweights this". research/pitching.py replaces it with a
projection blending prior season and season-to-date, shrunk by batters faced,
plus xwOBA-against and a velocity process measure. That work happens before any
test here, not after seeing a disappointing one.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import paths
from research.market_panel import game_panel
from research.pitching import build as build_pitching
from research.part_e_prices import POPULAR
from research.stats import logit, expit, logloss, block_bootstrap, fmt, SEED

# Home-minus-away differentials, each signed so that POSITIVE HELPS THE HOME
# TEAM. Without that convention the coefficient signs in E1 are unreadable and
# "the market overvalues starters" has no direction.
# Velocity CHANGE against the prior season needs a prior season: 200+ tracked
# fastballs from the same pitcher. That exists for about half of all starts,
# and requiring it from BOTH starters would drop the sample from ~6,400 games
# to ~2,950 - more than half the power in every test here, spent on one
# secondary process measure.
#
# So the feature set is split. CORE is what E1's actual hypothesis needs
# (starter quality vs bullpen quality and fatigue) and is ~99% complete.
# Velocity is reported as its own term, on the games that have it, and is the
# subject of E6 anyway. This split was decided on the COVERAGE NUMBERS, before
# any coefficient in this file had been looked at; E1 is reported both ways so
# the choice cannot hide anything.
CORE = ["d_sp_kbb", "d_sp_xwoba", "d_pen_kbb", "d_pen_fatigue",
        "d_pen_third_day", "d_off_woba", "d_rest", "park_factor"]
VELO = "d_sp_velo_delta"
FULL = CORE[:2] + [VELO] + CORE[2:]
STARTER_TERMS = ["d_sp_kbb", "d_sp_xwoba", VELO]
PEN_TERMS = ["d_pen_kbb", "d_pen_fatigue", "d_pen_third_day"]


def frame() -> pd.DataFrame:
    """Market panel + proper pitching measures, one row per game."""
    measures, _ = build_pitching()
    tr = pd.read_parquet(paths.training_table("mlb"))
    tr = tr[["game_id", "game_pk", "home_ab", "away_ab", "season",
             "home_off_woba_30d", "away_off_woba_30d", "home_rest_days",
             "away_rest_days", "park_factor"]]

    cols = ["sp_kbb", "sp_xwoba", "sp_velo", "sp_velo_delta", "pen_kbb_lev",
            "pen_fatigue_pitches", "pen_third_day_arms"]
    m = measures[["game_pk", "pitch_team"] + cols]
    for side, ab in (("home", "home_ab"), ("away", "away_ab")):
        tr = tr.merge(m.rename(columns={c: f"{side}_{c}" for c in cols}
                               | {"pitch_team": ab}),
                      on=["game_pk", ab], how="left")

    p = game_panel()
    d = p.merge(tr, on=["game_id", "season"], how="inner")

    # Sign every difference so that positive favours the home team.
    d["d_sp_kbb"] = d["home_sp_kbb"] - d["away_sp_kbb"]
    d["d_sp_xwoba"] = d["away_sp_xwoba"] - d["home_sp_xwoba"]   # lower better
    d["d_sp_velo_delta"] = d["home_sp_velo_delta"] - d["away_sp_velo_delta"]
    d["d_pen_kbb"] = d["home_pen_kbb_lev"] - d["away_pen_kbb_lev"]
    d["d_pen_fatigue"] = (d["away_pen_fatigue_pitches"]
                          - d["home_pen_fatigue_pitches"])          # tired opp
    d["d_pen_third_day"] = (d["away_pen_third_day_arms"]
                            - d["home_pen_third_day_arms"])
    d["d_off_woba"] = d["home_off_woba_30d"] - d["away_off_woba_30d"]
    d["d_rest"] = d["home_rest_days"] - d["away_rest_days"]

    d["day"] = pd.to_datetime(d["game_date"]).dt.strftime("%Y-%m-%d")
    d["month"] = pd.to_datetime(d["game_date"]).dt.month
    d["l_close"] = logit(d["p_fair_close"])
    d["l_morning"] = logit(d["p_fair_morning"])
    d = d.dropna(subset=CORE + ["l_close", "home_won"]).copy()

    # Standardise so coefficients are "per standard deviation", which is the
    # only scale on which a starter term and a bullpen term can be compared.
    for c in FULL:
        d["z_" + c] = (d[c] - d[c].mean()) / d[c].std()
    return d


# -------------------------------------------------------------- fitting ----

def _ols(X, y):
    return np.linalg.lstsq(np.c_[np.ones(len(X)), X], y, rcond=None)[0]


def _logit_fit(X, y, offset=None):
    off = np.zeros(len(X)) if offset is None else np.asarray(offset)
    Xc = np.c_[np.ones(len(X)), X]

    def nll(b):
        p = np.clip(expit(off + Xc @ b), 1e-12, 1 - 1e-12)
        return -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))

    return minimize(nll, np.zeros(Xc.shape[1]), method="BFGS").x


def _boot_coefs(d, feats, fn, n_boot=600, seed=SEED):
    """Day-block bootstrap of any coefficient vector."""
    days = d["day"].values
    uniq = np.unique(days)
    idx = {u: np.where(days == u)[0] for u in uniq}
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(uniq), len(uniq))
        ii = np.concatenate([idx[uniq[j]] for j in pick])
        out.append(fn(d.iloc[ii], feats))
    return np.array(out)


def _market_coefs(d, feats):
    return _ols(d[["z_" + f for f in feats]].values, d["l_close"].values)


def _reality_coefs(d, feats):
    return _logit_fit(d[["z_" + f for f in feats]].values,
                      d["home_won"].values)


def _residual_coefs(d, feats):
    return _logit_fit(d[["z_" + f for f in feats]].values,
                      d["home_won"].values, offset=d["l_close"].values)


# ------------------------------------------------------------------- E1 ----

def e1(d: pd.DataFrame, feats=None, tag="CORE features"):
    feats = feats or CORE
    d = d.dropna(subset=feats).copy()
    print("=" * 78)
    print(f"E1  the market's recipe vs reality's recipe  [{tag}]")
    print("=" * 78)
    print(f"\n{len(d):,} games, {d['season'].nunique()} seasons")
    print("coefficients are per standard deviation, signed so POSITIVE HELPS "
          "THE HOME TEAM\n")

    mk = _market_coefs(d, feats)
    rl = _reality_coefs(d, feats)
    mk_b = _boot_coefs(d, feats, _market_coefs)
    rl_b = _boot_coefs(d, feats, _reality_coefs)

    print("(1) vs (2): descriptive comparison of the two recipes")
    print(f"  {'feature':18s} {'market':>9s} {'reality':>9s} "
          f"{'market-reality':>16s} {'t':>7s}   pre-registered")
    pre = {"d_sp_kbb": "market larger", "d_sp_xwoba": "market larger",
           "d_sp_velo_delta": "market larger", "d_pen_kbb": "market smaller",
           "d_pen_fatigue": "market smaller",
           "d_pen_third_day": "market smaller"}
    for i, f in enumerate(feats):
        diff_b = mk_b[:, i + 1] - rl_b[:, i + 1]
        t = (mk[i + 1] - rl[i + 1]) / diff_b.std(ddof=1)
        want = pre.get(f, "")
        got = "market larger" if mk[i + 1] > rl[i + 1] else "market smaller"
        mark = ""
        if want:
            mark = "  as predicted" if want == got else "  WRONG SIGN"
        print(f"  {f:18s} {mk[i + 1]:>+9.4f} {rl[i + 1]:>+9.4f} "
              f"{mk[i + 1] - rl[i + 1]:>+16.4f} {t:>+7.2f}   {want}{mark}")

    print("\n(3) THE DECISIVE TEST: does the feature predict the outcome ON TOP")
    print("    of the closing price? Null is exactly zero.")
    rs = _residual_coefs(d, feats)
    rs_b = _boot_coefs(d, feats, _residual_coefs)
    print(f"  {'feature':18s} {'coef':>9s} {'95% CI':>22s} {'t':>7s}   "
          f"pre-registered")
    pre3 = {f: "negative" for f in STARTER_TERMS}
    pre3.update({f: "positive" for f in PEN_TERMS})
    for i, f in enumerate(feats):
        b = rs_b[:, i + 1]
        lo, hi = np.percentile(b, [2.5, 97.5])
        t = rs[i + 1] / b.std(ddof=1)
        want = pre3.get(f, "")
        mark = ""
        if want:
            got = "positive" if rs[i + 1] > 0 else "negative"
            ok = (want == got) and abs(t) > 2
            mark = ("  PASSES" if ok
                    else ("  wrong sign" if want != got else "  n.s."))
        print(f"  {f:18s} {rs[i + 1]:>+9.4f} [{lo:>+9.4f},{hi:>+9.4f}] "
              f"{t:>+7.2f}   {want}{mark}")

    print("\n  what the largest residual coefficient is worth, in probability:")
    big = int(np.argmax(np.abs(rs[1:])))
    per_sd = abs(rs[big + 1]) * 0.25 * 100
    print(f"    {feats[big]}: {abs(rs[big + 1]):.4f} logit per sd = "
          f"{per_sd:.2f} probability points per sd near a coin flip.")
    print(f"    A NJ book's margin is about 4 points, so a bet needs ~2 "
          f"points.")
    print(f"    This is worth {per_sd / 2.0:.2f}x the vig at one sd, "
          f"{2 * per_sd / 2.0:.2f}x at two.")
    return rs, rs_b


# ------------------------------------------------------------------- E2 ----

def e2(d: pd.DataFrame):
    print("\n" + "=" * 78)
    print("E2  subgroup calibration")
    print("=" * 78)
    print("\npre-registered: home teams with a LARGE STARTER EDGE are")
    print("OVERPRICED; teams facing a TIRED bullpen are UNDERPRICED.\n")
    for col, name in (("d_sp_kbb", "starter K-BB% edge, home minus away"),
                      ("d_pen_fatigue", "opponent bullpen fatigue edge"),
                      ("p_fair_close", "favourite size (home)")):
        print(f"--- bucketed by {name} ---")
        q = pd.qcut(d[col], 5, duplicates="drop")
        print(f"  {'bucket':>22s} {'n':>6s} {'implied':>9s} {'actual':>9s} "
              f"{'gap':>8s} {'t':>7s} {'EV@-110':>9s}")
        for bk, g in d.groupby(q, observed=True):
            r = block_bootstrap((g["home_won"] - g["p_fair_close"]).values,
                                g["day"].values, n_boot=1000)
            # what betting the home side at a typical -110 NJ price returns
            ev = g["home_won"].mean() * (1 + 100 / 110) - 1
            print(f"  {str(bk):>22s} {len(g):>6,} "
                  f"{100 * g['p_fair_close'].mean():>8.2f}% "
                  f"{100 * g['home_won'].mean():>8.2f}% "
                  f"{100 * r['mean']:>+7.2f} {r['t']:>+7.2f} "
                  f"{100 * ev:>+8.2f}%")
        print()


# ------------------------------------------------------------------- E3 ----

def _streaks(d: pd.DataFrame) -> pd.DataFrame:
    """Entering win streak for each team, strictly before the game."""
    rows = []
    for _, g in d.sort_values("game_date").iterrows():
        rows.append((g["game_id"], g["game_date"], g["home"], g["home_won"]))
        rows.append((g["game_id"], g["game_date"], g["away"],
                     1 - g["home_won"]))
    t = pd.DataFrame(rows, columns=["game_id", "game_date", "team", "won"])
    t = t.sort_values(["team", "game_date"])
    # streak entering this game = consecutive prior wins
    t["prev_won"] = t.groupby("team")["won"].shift(1)
    grp = (t["prev_won"] != t.groupby("team")["prev_won"].shift(1)).cumsum()
    t["streak"] = (t.groupby(["team", grp]).cumcount() + 1) * t["prev_won"]
    return t


def e3(d: pd.DataFrame):
    print("\n" + "=" * 78)
    print("E3  does public money push lines the wrong way?")
    print("=" * 78)
    both = d.dropna(subset=["l_morning"]).copy()
    ll_m = logloss(both["p_fair_morning"], both["home_won"])
    ll_c = logloss(both["p_fair_close"], both["home_won"])
    r = block_bootstrap(ll_m - ll_c, both["day"].values, n_boot=4000)
    print(f"\nBASELINE, all {len(both):,} games. Positive = the CLOSE is "
          f"better,\nwhich is close to a law of markets and doubles as a "
          f"check that this\npipeline is wired up correctly.")
    print("  morning minus close log loss: " + fmt(r, places=6))

    st = _streaks(d)
    hot = set(st.loc[st["streak"] >= 5, "game_id"])

    marquee = d["d_sp_kbb"].abs() >= d["d_sp_kbb"].abs().quantile(0.90)
    big = d["home"].isin(POPULAR) | d["away"].isin(POPULAR)
    heavy = (d["p_fair_close"] >= 2 / 3) | (d["p_fair_close"] <= 1 / 3)
    hotg = d["game_id"].isin(hot)

    print("\npre-registered: overall the close wins; in a public subgroup the")
    print("gap NARROWS, and in at least one the MORNING price is better.\n")
    print(f"  {'subgroup':28s} {'n':>6s} {'morning-close':>14s} {'t':>7s}")
    for name, mask in (("all games", pd.Series(True, index=d.index)),
                       ("marquee starter matchup", marquee),
                       ("a big-market team", big),
                       ("heavy favourite either side", heavy),
                       ("a team on a 5+ win streak", hotg)):
        g = d[mask].dropna(subset=["l_morning"])
        if len(g) < 100:
            continue
        diff = (logloss(g["p_fair_morning"], g["home_won"])
                - logloss(g["p_fair_close"], g["home_won"]))
        r = block_bootstrap(diff, g["day"].values, n_boot=2000)
        print(f"  {name:28s} {len(g):>6,} {r['mean']:>+14.6f} {r['t']:>+7.2f}")
    print("\n  A NEGATIVE number is the pre-registered finding: the morning")
    print("  price beat the close, so the move was noise.")


# ------------------------------------------------------------------- E6 ----

def e6(d: pd.DataFrame):
    print("\n" + "=" * 78)
    print("E6  early-season slowness")
    print("=" * 78)
    print("\npre-registered: the market residual is MORE predictable in")
    print("April-May than in July-August, and a velocity DROP predicts that")
    print("pitcher's team UNDERPERFORMING its closing price.\n")

    early = d[d["month"].isin([3, 4, 5])]
    late = d[d["month"].isin([7, 8])]
    print(f"  April-May {len(early):,} games   July-August {len(late):,} games")

    print("\n--- residual predictability: all features, on top of the close ---")
    for name, g in (("April-May", early), ("July-August", late)):
        if len(g) < 200:
            continue
        rs = _residual_coefs(g, CORE)
        b = _boot_coefs(g, CORE, _residual_coefs, n_boot=400)
        # one number for "how much is there": the improvement in log loss from
        # adding the features to the price, in-sample (so it is an upper bound)
        p0 = g["p_fair_close"].values
        X = g[["z_" + f for f in CORE]].values
        p1 = expit(logit(p0) + np.c_[np.ones(len(X)), X] @ rs)
        gain = logloss(p0, g["home_won"]).mean() - logloss(p1,
                                                           g["home_won"]).mean()
        strongest = int(np.argmax(np.abs(rs[1:] / b[:, 1:].std(0, ddof=1))))
        tt = rs[strongest + 1] / b[:, strongest + 1].std(ddof=1)
        print(f"  {name:12s} in-sample log-loss gain {gain:+.6f}   "
              f"strongest term {CORE[strongest]} t={tt:+.2f}")
    print("  (in-sample, so both are upper bounds - the comparison between")
    print("   the two windows is the point, not either number by itself)")

    print("\n--- the specific velocity test ---")
    v = d.dropna(subset=["d_sp_velo_delta"])
    for name, g in (("April-May", v[v["month"].isin([3, 4, 5])]),
                    ("July-August", v[v["month"].isin([7, 8])]),
                    ("whole season", v)):
        if len(g) < 200:
            continue
        rs = _residual_coefs(g, ["d_sp_velo_delta"])
        b = _boot_coefs(g, ["d_sp_velo_delta"], _residual_coefs, n_boot=800)
        t = rs[1] / b[:, 1].std(ddof=1)
        lo, hi = np.percentile(b[:, 1], [2.5, 97.5])
        print(f"  {name:12s} n={len(g):>5,}  coef {rs[1]:>+8.4f} "
              f"[{lo:>+7.4f},{hi:>+7.4f}]  t={t:>+5.2f}")
    print("\n  POSITIVE means a home starter throwing harder than last year")
    print("  beats his closing price, i.e. a velocity DROP predicts")
    print("  underperformance - the pre-registered direction.")


if __name__ == "__main__":
    d = frame()
    e1(d, CORE, "CORE features")
    e1(d, FULL, "FULL features, incl. velocity change")
    e2(d)
    e3(d)
    e6(d)
