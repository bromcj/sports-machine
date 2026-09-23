"""E4 and E5: what the prices alone say. Free.

    python research/part_e_prices.py

Both hypotheses are pre-registered in docs/experiments.md. They are run
together because neither needs a model, a feature or a pitching measure - only
prices, and in E4's case not even outcomes.

E4 IS THE HIGHEST-POWERED THING IN THIS WHOLE PROJECT and it is worth saying
why. Every other test is limited by the noise in a coin flip: the standard
error on a win rate is about 0.5/sqrt(n), so 2,500 games can only reveal a
2-point bias. E4 compares a price to a price. There is no coin flip in it. If
DraftKings is a point and a half off Pinnacle on Yankees games, that shows up
with certainty over a few hundred games, because both numbers are observed
exactly.

The catch is that being off the sharp line is only worth money if it is off by
more than the book charges to take the bet. So everything here is reported as
EV against the actual posted American odds, not as a de-vigged gap:

    EV  =  p_fair(Pinnacle, de-vigged)  x  decimal odds at the soft book  -  1

which is positive only when the soft book is paying more than the sharp book
thinks the outcome is worth, AFTER its own margin.

E5 is the opposite case: a known effect, included deliberately as a calibration
of whether these tests can find anything at all. If the favourite-longshot bias
is absent everywhere, that is partly evidence about our power and not only
about the market.

PRE-REGISTERED DIRECTIONS (copied here so they cannot drift):
  E4  each NJ book prices POPULAR teams higher than Pinnacle does, and the gap
      is LARGER on weekends and LARGER further from first pitch.
  E5  heavy favourites (-200 and beyond) win MORE often than implied; near zero
      at Pinnacle, small but positive at the NJ books.

ONE LIMIT, STATED UP FRONT. We hold four books. A real line shop watches eight
to fifteen. Everything E4 concludes about how much there is to shop is a
statement about DraftKings, FanDuel and BetMGM against Pinnacle, and is a lower
bound on what a wider shop would find - not a measurement of the NJ market.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from bets.engine import american_to_prob, american_to_decimal
from research.market_panel import game_panel, book_long, SOFT, SHARP
from research.stats import block_bootstrap, fmt

# Pre-registered in docs/experiments.md E4, before looking at any gap.
POPULAR = ["New York Yankees", "Los Angeles Dodgers", "Boston Red Sox",
           "Chicago Cubs", "Atlanta Braves", "New York Mets",
           "Philadelphia Phillies"]


def team_rows(long: pd.DataFrame) -> pd.DataFrame:
    """One row per (game, book, TEAM). The unit E4 is actually about.

    A game-level "home probability gap" cannot answer "is this book short on
    the Yankees", because the Yankees are the home team half the time and the
    sign flips. Splitting to team level first makes the question askable.
    """
    keep = ["game_id", "game_date", "season", "ts", "book", "lead_min",
            "vig", "home_won", "away", "home"]
    home = long[keep].copy()
    home["team"] = long["home"]
    home["is_home"] = 1
    home["p_book"] = long["p_home"]
    home["ml"] = long["home_ml"]
    home["won"] = long["home_won"]

    away = long[keep].copy()
    away["team"] = long["away"]
    away["is_home"] = 0
    away["p_book"] = long["p_away"]
    away["ml"] = long["away_ml"]
    away["won"] = 1 - long["home_won"]

    t = pd.concat([home, away], ignore_index=True)
    t["decimal"] = [american_to_decimal(x) for x in t["ml"]]
    t["p_raw"] = [american_to_prob(x) for x in t["ml"]]
    t["popular"] = t["team"].isin(POPULAR)
    t["dow"] = pd.to_datetime(t["game_date"]).dt.dayofweek
    t["weekend"] = t["dow"].isin([4, 5, 6])          # Fri/Sat/Sun
    return t


def sharp_vs_soft(long: pd.DataFrame) -> pd.DataFrame:
    """Attach Pinnacle's de-vigged probability to every soft-book price.

    Joined on (game, ts, team): the SAME request, so the two books are compared
    at the same instant. Comparing a soft price at noon with a sharp price at
    7pm would measure the passage of time, not the difference between books.
    """
    t = team_rows(long)
    pin = (t[t["book"] == SHARP][["game_id", "ts", "team", "p_book", "vig"]]
           .rename(columns={"p_book": "p_pin", "vig": "vig_pin"}))
    soft = t[t["book"].isin(SOFT)]
    d = soft.merge(pin, on=["game_id", "ts", "team"], how="inner")
    d["gap"] = d["p_book"] - d["p_pin"]        # + = book has team likelier
    d["ev"] = d["p_pin"] * d["decimal"] - 1.0  # what a bet is actually worth
    return d


# ------------------------------------------------------------------- E4 ----

def e4(long: pd.DataFrame):
    d = sharp_vs_soft(long)
    print("=" * 78)
    print("E4  where do the NJ books sit off the sharp line?")
    print("=" * 78)
    print(f"\n{len(d):,} (game, request, book, team) prices with a Pinnacle "
          f"price at the same instant, across {d['game_id'].nunique():,} "
          f"games\n")

    print("overall gap to Pinnacle, in probability points "
          "(+ = book rates the team higher than Pinnacle)")
    for b in SOFT:
        g = d[d["book"] == b]
        print(f"  {b:11s} mean {100 * g['gap'].mean():+.3f}  "
              f"sd {100 * g['gap'].std():.3f}  "
              f"mean |gap| {100 * g['gap'].abs().mean():.3f}  n={len(g):,}")
    print("  (the mean is ~0 by construction: proportional de-vigging forces")
    print("   p_home + p_away = 1 at every book, so a team's gap is exactly")
    print("   minus its opponent's. Only the PATTERN of the sign is a finding.)")

    print("\n--- PRE-REGISTERED: popular teams priced HIGHER than Pinnacle ---")
    print(f"{'book':12s} {'popular':>10s} {'other':>10s} {'diff':>10s} "
          f"{'t':>7s}   verdict")
    for b in SOFT:
        g = d[d["book"] == b]
        pop, oth = g[g["popular"]], g[~g["popular"]]
        # test the DIFFERENCE directly: popular gaps against negated others, so
        # one bootstrap mean is exactly the contrast being claimed
        r = block_bootstrap(
            np.r_[pop["gap"].values, -oth["gap"].values],
            np.r_[pop["game_id"].values, oth["game_id"].values], n_boot=1500)
        diff = pop["gap"].mean() - oth["gap"].mean()
        verdict = "as predicted" if diff > 0 else "WRONG SIGN"
        print(f"{b:12s} {100 * pop['gap'].mean():>+10.3f} "
              f"{100 * oth['gap'].mean():>+10.3f} {100 * diff:>+10.3f} "
              f"{r['t']:>+7.2f}   {verdict}")

    print("\n--- PRE-REGISTERED: gap larger on weekends ---")
    print(f"{'book':12s} {'Fri-Sun':>10s} {'Mon-Thu':>10s} {'diff':>10s}")
    for b in SOFT:
        g = d[d["book"] == b]
        we, wd = g[g["weekend"]], g[~g["weekend"]]
        print(f"{b:12s} {100 * we['gap'].abs().mean():>10.3f} "
              f"{100 * wd['gap'].abs().mean():>10.3f} "
              f"{100 * (we['gap'].abs().mean() - wd['gap'].abs().mean()):>+10.3f}")

    print("\n--- PRE-REGISTERED: gap larger further from first pitch ---")
    bins = [0, 60, 180, 360, 720, 1e9]
    lbl = ["<1h", "1-3h", "3-6h", "6-12h", ">12h"]
    d["leadbin"] = pd.cut(d["lead_min"], bins=bins, labels=lbl)
    print(d.pivot_table(index="leadbin", columns="book", values="gap",
                        aggfunc=lambda s: 100 * s.abs().mean(),
                        observed=True).round(3).to_string())

    print("\n--- by favourite size (mean gap; antisymmetric by construction) ---")
    d["favbin"] = pd.cut(d["p_pin"], [0, .35, .45, .55, .65, 1.0],
                         labels=["<35%", "35-45%", "45-55%", "55-65%", ">65%"])
    print(d.pivot_table(index="favbin", columns="book", values="gap",
                        aggfunc=lambda s: 100 * s.mean(),
                        observed=True).round(3).to_string())

    print("\n" + "-" * 78)
    print("BETTABLE: EV against the posted price, after the book's own vig")
    print("-" * 78)
    print("\nmean EV per unit if you bet EVERY price at that book:")
    for b in SOFT:
        g = d[d["book"] == b]
        print(f"  {b:11s} {100 * g['ev'].mean():+.3f}%   "
              f"share of prices with EV>0: {100 * (g['ev'] > 0).mean():.1f}%")

    print("\nmean EV on the prices that ARE +EV, and how often they appear:")
    print(f"{'book':12s} {'n +EV':>8s} {'per game':>9s} {'mean EV':>9s} "
          f"{'>1.5% EV':>9s} {'>3% EV':>8s}")
    ngames = d["game_id"].nunique()
    for b in SOFT:
        g = d[d["book"] == b]
        pos = g[g["ev"] > 0]
        print(f"{b:12s} {len(pos):>8,} {len(pos) / ngames:>9.2f} "
              f"{100 * pos['ev'].mean():>+8.2f}% "
              f"{(g['ev'] > .015).sum():>9,} {(g['ev'] > .03).sum():>8,}")

    print("\nrestricted to prices within 90 min of first pitch - what a live "
          "shop\nwould actually be looking at:")
    close = d[d["lead_min"] <= 90]
    for b in SOFT:
        g = close[close["book"] == b]
        if len(g) == 0:
            continue
        pos = g[g["ev"] > 0.015]
        r = block_bootstrap(g["ev"].values, g["game_id"].values, n_boot=1500)
        print(f"  {b:11s} prices {len(g):>6,}  EV>1.5% on {len(pos):>5,} "
              f"({100 * len(pos) / len(g):.2f}%)   mean EV "
              + fmt(r, scale=100, places=3))
    return d


# ------------------------------------------------------------------- E5 ----

def e5(panel: pd.DataFrame, long: pd.DataFrame):
    """Favourite-longshot bias, with the two traps this test sets avoided.

    TRAP 1: DOUBLE COUNTING. Proportional de-vigging forces p_fav + p_dog = 1
    exactly, so "longshots lose more than implied" is not a second observation
    - it is the same observation with the sign flipped. Bucketing both sides
    produces a table that is mirror-symmetric by construction and looks like
    twice the evidence there is. Only the favourite side is reported.

    TRAP 2: BOOK-DEPENDENT SAMPLES. Selecting "heavy favourites" at each book
    using that book's own de-vigged probability selects DIFFERENT GAMES at each
    book, because the books disagree slightly and de-vigging bends favourites
    by an amount that scales with the book's margin. Pinnacle finds 536 of
    them, BetMGM 462 - and BetMGM's are the stronger ones. That biases any
    comparison between books before a single outcome is looked at. The sample
    is fixed once, using Pinnacle, and every book is judged on it.

    What survives both fixes is the only version of the question that means
    anything: on one fixed set of heavy favourites, (a) is the SHARP line
    wrong, and (b) would betting them at each book's posted price - vig
    included, no de-vigging anywhere - have made money.
    """
    print("\n" + "=" * 78)
    print("E5  favourite-longshot bias")
    print("=" * 78)
    t = team_rows(long)
    close = t.merge(panel[["game_id", "close_ts"]], on="game_id")
    close = close[close["ts"] == close["close_ts"]]

    pin = (close[close["book"] == SHARP][["game_id", "team", "p_book"]]
           .rename(columns={"p_book": "p_pin"}))
    close = close.merge(pin, on=["game_id", "team"], how="inner")
    fav = close[close["p_pin"] > 0.5].copy()     # one row per game per book
    print(f"\n{fav['game_id'].nunique():,} games with a Pinnacle closing "
          f"price; the favourite side of each")

    edges = [0.5, 0.55, 0.6, 2 / 3, 0.75, 1.0]
    labels = ["-100..-122", "-122..-150", "-150..-200", "-200..-300", "<-300"]
    fav["bucket"] = pd.cut(fav["p_pin"], edges, labels=labels, right=False)

    print("\n--- is the SHARP line itself wrong about favourites? ---")
    print("(this is the market-bias question; the soft books cannot cause it)")
    g = fav[fav["book"] == SHARP]
    print(f"  {'bucket':>12s} {'games':>6s} {'implied':>9s} {'actual':>9s} "
          f"{'gap':>8s} {'t':>7s}")
    for bk, gg in g.groupby("bucket", observed=True):
        r = block_bootstrap((gg["won"] - gg["p_book"]).values,
                            gg["game_id"].values, n_boot=1500)
        print(f"  {str(bk):>12s} {len(gg):>6,} "
              f"{100 * gg['p_book'].mean():>8.2f}% "
              f"{100 * gg['won'].mean():>8.2f}% {100 * r['mean']:>+7.2f} "
              f"{r['t']:>+7.2f}")

    heavy_ids = set(fav.loc[fav["p_pin"] >= 2 / 3, "game_id"])
    hg = g[g["game_id"].isin(heavy_ids)]
    r = block_bootstrap((hg["won"] - hg["p_book"]).values,
                        hg["game_id"].values, n_boot=4000)
    print("\n  PRE-REGISTERED, -200 and beyond, at Pinnacle: "
          + fmt(r, scale=100, places=2))
    print(f"  for scale: the standard error of a win rate over {len(hg):,} "
          f"games is about\n  {100 * 0.5 / np.sqrt(len(hg)):.2f} points, so "
          f"only a very large bias could show up here at all.")

    print("\n--- the same fixed games, at each book's de-vigged price ---")
    print("(differences between books here are mostly the de-vig method")
    print(" interacting with the size of each book's margin, not a market fact)")
    for b in [SHARP] + SOFT:
        gg = fav[(fav["book"] == b) & fav["game_id"].isin(heavy_ids)]
        if len(gg) < 50:
            continue
        print(f"  {b:11s} n={len(gg):>4,}  implied "
              f"{100 * gg['p_book'].mean():.2f}%   actual "
              f"{100 * gg['won'].mean():.2f}%   gap "
              f"{100 * (gg['won'].mean() - gg['p_book'].mean()):+.2f}   "
              f"(mean vig {100 * gg['vig'].mean():.2f}%)")

    print("\n--- BETTABLE: bet every heavy favourite at the posted price ---")
    print("no de-vigging anywhere; this is money in and money out")
    for b in [SHARP] + SOFT:
        gg = fav[(fav["book"] == b) & fav["game_id"].isin(heavy_ids)]
        if len(gg) < 50:
            continue
        roi = gg["won"] * (gg["decimal"] - 1) - (1 - gg["won"])
        r = block_bootstrap(roi.values, gg["game_id"].values, n_boot=4000)
        print(f"  {b:11s} bets {len(gg):>4,}  ROI "
              + fmt(r, scale=100, places=2))


if __name__ == "__main__":
    long = book_long()
    panel = game_panel()
    e4(long)
    e5(panel, long)
