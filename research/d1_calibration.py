"""D1 / 3.6: is the home intercept worth applying, judged against the real market?

    python research/d1_calibration.py

BACKGROUND. `margin_to_win_prob` is sigmoid(k * margin) with no intercept, so
it returns exactly 0.500 whenever the predicted run margin is zero. Home teams
win about 53% of games, and the model's mean predicted margin is about +0.04
runs, so it under-rates home teams by roughly 2.9 points in every season.
docs/calibration-3.6.md measured that and proposed sigmoid(a + k * margin), with
`a` fitted leave-one-season-out inside the training window.

It was NOT applied, for three stated reasons. Two have now expired and one is
what this file settles:

  1. "the brief says to ask"            - asked and answered
  2. "it would invalidate the paper run" - the paper sample is 10 bets placed
                                           today, none settled. Nothing to
                                           protect. (Checked, not assumed.)
  3. "measure it against a real market first" - THIS. The original -0.001649
     was measured against a home-rate placeholder. A correction that helps
     against a constant is not guaranteed to help against a de-vigged closing
     line, because the market already knows about home-field advantage and
     the model's error may simply not be where the placeholder said it was.

THE BAR, taken from NEXT_TASK.md Part D: apply only if it helps in EVERY
season. Not on average - every one. A correction that helps twice and hurts
once is a correction fitted to two seasons.

Note on what is being compared. Both variants come out of the SAME
walk-forward, and `a` is fitted leave-one-season-out inside the training window
only, so the intercept applied to 2026 never saw a 2026 game. This is not the
model being re-fit; it is one number being added to an existing prediction.
"""
import sys
from pathlib import Path


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from db import connect
from research.a1_moneyline import build
from research.stats import logloss, block_bootstrap, leave_one_out, fmt


def paper_sample_status() -> None:
    """Reason 2 above, checked rather than assumed."""
    con = connect()
    rows = con.execute(
        "SELECT mode, COUNT(*) n, SUM(result IS NOT NULL) settled,"
        " SUM(clv_pct IS NOT NULL) graded, MIN(ts) first, MAX(ts) last"
        " FROM bets GROUP BY mode").fetchall()
    con.close()
    print("-" * 74)
    print("does applying this disturb the paper-trading sample?")
    print("-" * 74)
    if not rows:
        print("  no bets at all.")
        return
    for r in rows:
        print(f"  {r['mode']:9s} {r['n']:>3} bets, {r['settled'] or 0} settled,"
              f" {r['graded'] or 0} with CLV   first {r['first'][:16]}")
    print("\n  Gate 2 counts only bets with a CLV measurement. Any bet without"
          "\n  one contributes nothing to it, so restarting costs nothing.")


def report():
    d = build()
    print("=" * 74)
    print("D1  home intercept, measured against the real de-vigged close")
    print("=" * 74)
    print(f"\n{len(d):,} games, out-of-sample, walk-forward")
    print("intercept fitted leave-one-season-out inside the training window:")
    print(d.groupby("season")["intercept"].first().round(4).to_string())

    ll_raw = logloss(d["p_model_raw"], d["home_won"])
    ll_int = logloss(d["p_model"], d["home_won"])
    ll_mkt = logloss(d["p_fair_close"], d["home_won"])
    d = d.assign(ll_raw=ll_raw, ll_int=ll_int, ll_mkt=ll_mkt)

    print("\n" + "-" * 74)
    print("THE BAR: does it help in EVERY season?")
    print("-" * 74)
    print(f"\n{'season':>7s} {'n':>6s} {'no intercept':>13s} {'with':>11s} "
          f"{'change':>10s} {'helps?':>8s}")
    every = True
    for s, g in d.groupby("season"):
        a, b = g["ll_raw"].mean(), g["ll_int"].mean()
        ok = b < a
        every &= ok
        print(f"{s:>7d} {len(g):>6,} {a:>13.6f} {b:>11.6f} {b - a:>+10.6f} "
              f"{'yes' if ok else 'NO':>8s}")

    diff = d["ll_raw"] - d["ll_int"]        # positive = intercept better
    r = block_bootstrap(diff.values, d["day"].values, n_boot=4000)
    print(f"\npooled improvement (positive = intercept helps):")
    print("  " + fmt(r, places=6))
    print("\nleave-one-season-out:")
    for s, rr in leave_one_out(diff.values, d["day"].values,
                               d["season"].values).items():
        print(f"  drop {s}: " + fmt(rr, places=6))

    print("\n" + "-" * 74)
    print("what it does NOT change: the model still loses to the market")
    print("-" * 74)
    print(f"\n{'season':>7s} {'model+a':>10s} {'market':>10s} {'margin':>10s}")
    for s, g in d.groupby("season"):
        print(f"{s:>7d} {g['ll_int'].mean():>10.6f} {g['ll_mkt'].mean():>10.6f} "
              f"{g['ll_mkt'].mean() - g['ll_int'].mean():>+10.6f}")
    gap = block_bootstrap((d["ll_mkt"] - d["ll_int"]).values,
                          d["day"].values, n_boot=4000)
    print("\npooled, model-with-intercept minus market (negative = still "
          "losing):")
    print("  " + fmt(gap, places=6))

    print("\n" + "-" * 74)
    print("calibration, the thing the intercept is for")
    print("-" * 74)
    print(f"\n{'season':>7s} {'predicted':>11s} {'+intercept':>11s} "
          f"{'actual':>9s}")
    for s, g in d.groupby("season"):
        print(f"{s:>7d} {g['p_model_raw'].mean():>11.4f} "
              f"{g['p_model'].mean():>11.4f} {g['home_won'].mean():>9.4f}")

    print()
    paper_sample_status()

    print("\n" + "=" * 74)
    print("VERDICT: " + ("APPLY - helps in every season"
                         if every else
                         "DO NOT APPLY - fails the every-season bar"))
    print("=" * 74)
    return every, r


if __name__ == "__main__":
    report()
