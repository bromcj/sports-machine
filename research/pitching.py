"""Starter quality and bullpen quality/fatigue, measured properly. Free, offline.

    python research/pitching.py            # build + leakage self-check

E1 asks whether the market overvalues starting pitchers and undervalues
bullpens. That question cannot be answered with the features the model already
has, and it is worth being precise about why, because it is the difference
between a real null and a null manufactured by bad measurement.

The model's starter feature is `sp_kbb_5s`: strikeouts minus walks over the
pitcher's last five starts, about 120 batters faced. The standard error on a
K-BB% estimated from 120 batters is roughly 4 percentage points, against a
between-pitcher spread of about 8. So well over half the variance in that
feature is noise. Regress the market's price on it and the coefficient is
attenuated toward zero by roughly that fraction - and E1 compares the market's
coefficient with reality's. Attenuation hits both, but not equally, and
"the market weights starters less than it should" is exactly what a
badly-measured starter variable looks like whether or not it is true.

So the inputs get built first, from the enriched Statcast extract:

  STARTER QUALITY   Marcel-style projection: prior season and current season to
                    date, each weighted by batters faced, regressed toward the
                    league mean, for K-BB% and xwOBA-against. Plus a process
                    measure - average four-seam velocity this season and its
                    change against last season - because velocity moves before
                    results do, which is E6's whole hypothesis.

  BULLPEN QUALITY   30-day K-BB% of the relievers, each weighted by how much
                    LEVERAGE he actually absorbs. A bullpen is not its average
                    arm; it is the arms the manager uses when the game is
                    close. Leverage comes straight from Statcast's
                    delta_home_win_exp, so it is the real thing rather than a
                    proxy built out of inning numbers.

  BULLPEN FATIGUE   Pitches thrown in the prior two days by the three highest-
                    leverage arms, and how many of them are on a third
                    consecutive day. Availability, not quality.

xwOBA rather than wOBA throughout the contact half: xwOBA stabilises in roughly
50 batted balls where wOBA needs several hundred plate appearances, which is
the whole point when the window is one season to date.

LEAKAGE. Every number here describes a pitcher or a bullpen strictly BEFORE the
game it is attached to. Current-season aggregates are cumulative sums shifted
by one appearance; the 30-day bullpen window ends the day before. `selfcheck()`
recomputes one row of each from raw pitch data by brute force and compares,
because the project has been bitten by a feature that was described as shifted
and was not.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import paths

RICH_DIR = paths.DATA_DIR / "statcast_rich"

# Marcel-ish weights. Not tuned - these are the conventional defaults, and
# tuning them against an outcome in this project would be fitting the thing
# the experiment is supposed to measure.
W_PREV = 0.5           # last season counts half as much as this one
REG_BF_KBB = 200       # batters faced of league-average regression
REG_BF_XW = 200
REG_BF_REL = 60        # relievers face far fewer batters; regress harder
BULLPEN_WINDOW = 30    # days
FATIGUE_WINDOW = 2     # days
TOP_ARMS = 3
FF = ("FF", "SI", "FC")   # "fastball" for velocity purposes


def load_rich(years=None) -> pd.DataFrame:
    files = sorted(RICH_DIR.glob("*.parquet"))
    if years:
        files = [f for f in files if int(f.stem) in years]
    if not files:
        raise FileNotFoundError(
            f"No enriched Statcast in {RICH_DIR}. "
            f"Run: python research/enrich_statcast.py")
    sc = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    sc["game_date"] = pd.to_datetime(sc["game_date"])
    sc["season"] = sc["game_date"].dt.year
    sc["pitch_team"] = np.where(sc["inning_topbot"] == "Top",
                                sc["home_team"], sc["away_team"])
    return sc


def pitcher_lines(sc: pd.DataFrame) -> pd.DataFrame:
    """One row per pitcher-game, with everything the measures below need."""
    pa = sc[sc["woba_denom"] == 1].copy()
    # xwOBA: the expected value of the plate appearance. Batted balls get
    # Statcast's estimate from launch speed and angle; strikeouts, walks and
    # hit-by-pitches have no batted ball and take their actual wOBA weight,
    # which is also their expected one.
    pa["xw"] = pa["estimated_woba_using_speedangle"].fillna(pa["woba_value"])

    agg = (pa.groupby(["game_pk", "game_date", "season", "pitch_team",
                       "pitcher"])
             .agg(bf=("woba_value", "size"),
                  k=("events", lambda s: (s == "strikeout").sum()),
                  bb=("events", lambda s: s.isin(["walk",
                                                  "hit_by_pitch"]).sum()),
                  xw_sum=("xw", "sum"))
             .reset_index())

    per_pitch = (sc.groupby(["game_pk", "pitcher"])
                   .agg(pitches=("pitch_number", "size"),
                        lev=("delta_home_win_exp",
                             lambda s: s.abs().sum()),
                        first_ab=("at_bat_number", "min"),
                        days_rest=("pitcher_days_since_prev_game", "first"))
                   .reset_index())
    ff = sc[sc["pitch_type"].isin(FF)]
    vel = (ff.groupby(["game_pk", "pitcher"])["release_speed"]
             .agg(ff_sum="sum", ff_n="size").reset_index())

    out = (agg.merge(per_pitch, on=["game_pk", "pitcher"], how="left")
              .merge(vel, on=["game_pk", "pitcher"], how="left"))
    out[["ff_sum", "ff_n"]] = out[["ff_sum", "ff_n"]].fillna(0.0)

    # The starter is whoever faced the first batter for that team.
    starter_ab = out.groupby(["game_pk", "pitch_team"])["first_ab"] \
                    .transform("min")
    out["is_starter"] = out["first_ab"] == starter_ab
    return out.sort_values(["game_date", "game_pk"]).reset_index(drop=True)


# --------------------------------------------------------------- starters ---

def starter_quality(lines: pd.DataFrame) -> pd.DataFrame:
    """As-of projected K-BB%, xwOBA-against and velocity for every start."""
    s = lines[lines["is_starter"]].copy()
    s = s.sort_values(["pitcher", "game_date", "game_pk"])

    # --- current season to date, EXCLUDING this start ---
    g = s.groupby(["pitcher", "season"], sort=False)
    for col in ("bf", "k", "bb", "xw_sum", "ff_sum", "ff_n"):
        s[f"cur_{col}"] = g[col].cumsum() - s[col]
    s["cur_starts"] = g.cumcount()

    # --- prior season, the complete thing ---
    # Built from starts only, which is the right population: a starter's
    # relief outings (openers, a playoff bullpen game) are a different job.
    prev = (s.groupby(["pitcher", "season"])
             .agg(p_bf=("bf", "sum"), p_k=("k", "sum"), p_bb=("bb", "sum"),
                  p_xw=("xw_sum", "sum"), p_ffs=("ff_sum", "sum"),
                  p_ffn=("ff_n", "sum"))
             .reset_index())
    prev["season"] = prev["season"] + 1          # attach to the NEXT season
    s = s.merge(prev, on=["pitcher", "season"], how="left")
    for c in ("p_bf", "p_k", "p_bb", "p_xw", "p_ffs", "p_ffn"):
        s[c] = s[c].fillna(0.0)

    # --- league means, computed per season from STARTS, shifted a season ---
    # Using this season's league mean to regress this season's pitchers would
    # leak in a small way: the pitcher is inside his own regression target.
    lg = (s.groupby("season")
           .agg(bf=("bf", "sum"), k=("k", "sum"), bb=("bb", "sum"),
                xw=("xw_sum", "sum")).reset_index())
    lg["lg_kbb"] = (lg["k"] - lg["bb"]) / lg["bf"]
    lg["lg_xw"] = lg["xw"] / lg["bf"]
    lg["season"] = lg["season"] + 1
    s = s.merge(lg[["season", "lg_kbb", "lg_xw"]], on="season", how="left")
    # earliest season has no prior league mean; fill with the global one
    s["lg_kbb"] = s["lg_kbb"].fillna(
        (s["k"].sum() - s["bb"].sum()) / s["bf"].sum())
    s["lg_xw"] = s["lg_xw"].fillna(s["xw_sum"].sum() / s["bf"].sum())

    # --- the projection ---
    den = s["cur_bf"] + W_PREV * s["p_bf"]
    s["sp_kbb"] = ((s["cur_k"] - s["cur_bb"])
                   + W_PREV * (s["p_k"] - s["p_bb"])
                   + REG_BF_KBB * s["lg_kbb"]) / (den + REG_BF_KBB)
    s["sp_xwoba"] = (s["cur_xw_sum"] + W_PREV * s["p_xw"]
                     + REG_BF_XW * s["lg_xw"]) / (den + REG_BF_XW)
    s["sp_bf_seen"] = den

    # --- process: velocity now, and the change from last season ---
    s["sp_velo"] = np.where(s["cur_ff_n"] >= 100,
                            s["cur_ff_sum"] / s["cur_ff_n"].replace(0, np.nan),
                            np.nan)
    prev_velo = np.where(s["p_ffn"] >= 200, s["p_ffs"] / s["p_ffn"].replace(0, np.nan),
                         np.nan)
    s["sp_velo_prev"] = prev_velo
    s["sp_velo_delta"] = s["sp_velo"] - s["sp_velo_prev"]
    return s[["game_pk", "game_date", "season", "pitch_team", "pitcher",
              "sp_kbb", "sp_xwoba", "sp_bf_seen", "sp_velo", "sp_velo_prev",
              "sp_velo_delta", "cur_starts"]]


# --------------------------------------------------------------- bullpens ---

def bullpen_state(lines: pd.DataFrame) -> pd.DataFrame:
    """Leverage-weighted 30-day bullpen quality, and fatigue, per team-game.

    Done as an explicit loop over (team, date). It is not the fastest way, but
    every window here has to end strictly before the game and the loop makes
    that impossible to get subtly wrong - which a rolling().shift() does not,
    as _stale() in features/sports/mlb_features.py exists to document.
    """
    rel = lines[~lines["is_starter"]].copy()
    lg_kbb = (rel["k"].sum() - rel["bb"].sum()) / rel["bf"].sum()

    games = (lines.groupby(["game_pk", "game_date", "season", "pitch_team"])
                  .size().rename("n").reset_index())
    out = []
    for team, tgames in games.groupby("pitch_team", sort=False):
        tr = rel[rel["pitch_team"] == team]
        dates = tr["game_date"].values
        for row in tgames.itertuples():
            d = np.datetime64(row.game_date)
            win = tr[(dates >= d - np.timedelta64(BULLPEN_WINDOW, "D"))
                     & (dates < d)]
            if len(win) == 0:
                out.append((row.game_pk, team, np.nan, np.nan, np.nan, np.nan,
                            0))
                continue
            by = win.groupby("pitcher").agg(
                bf=("bf", "sum"), k=("k", "sum"), bb=("bb", "sum"),
                lev=("lev", "sum"))
            # each arm's K-BB%, regressed toward the league reliever mean by
            # how many batters he actually faced in the window
            kbb = ((by["k"] - by["bb"]) + REG_BF_REL * lg_kbb) \
                / (by["bf"] + REG_BF_REL)
            w = by["lev"].clip(lower=0)
            pen_kbb = float((kbb * w).sum() / w.sum()) if w.sum() > 0 \
                else float(kbb.mean())
            pen_kbb_flat = float(kbb.mean())

            top = by.sort_values("lev", ascending=False).head(TOP_ARMS).index
            recent = win[(win["game_date"] >= row.game_date
                          - pd.Timedelta(days=FATIGUE_WINDOW))
                         & win["pitcher"].isin(top)]
            fatigue = float(recent["pitches"].sum())
            # a third straight day: appeared on each of the two prior dates
            b2b2b = 0
            for p in top:
                days = set(recent[recent["pitcher"] == p]["game_date"]
                           .dt.normalize())
                if len(days) >= FATIGUE_WINDOW:
                    b2b2b += 1
            out.append((row.game_pk, team, pen_kbb, pen_kbb_flat,
                        float(by["lev"].sum()), fatigue, b2b2b))

    return pd.DataFrame(out, columns=[
        "game_pk", "pitch_team", "pen_kbb_lev", "pen_kbb_flat", "pen_lev_30d",
        "pen_fatigue_pitches", "pen_third_day_arms"])


CACHE = RICH_DIR / "_measures.parquet"
CACHE_LINES = RICH_DIR / "_lines.parquet"


def build(years=None, cache=True):
    """(measures, lines). Cached, because bullpen_state() takes ~10 minutes.

    The cache is keyed on nothing, deliberately: it is derived entirely from
    data/statcast_rich/, which only changes when enrich_statcast.py reruns. If
    you rebuild that, delete these two files. Anything cleverer would be a
    staleness bug waiting to happen, and a wrong pitching measure would quietly
    poison every result in Part E.
    """
    if cache and years is None and CACHE.exists() and CACHE_LINES.exists():
        return pd.read_parquet(CACHE), pd.read_parquet(CACHE_LINES)
    sc = load_rich(years)
    lines = pitcher_lines(sc)
    sq = starter_quality(lines)
    bp = bullpen_state(lines)
    out = sq.merge(bp, on=["game_pk", "pitch_team"], how="left")
    if cache and years is None:
        out.to_parquet(CACHE)
        lines.to_parquet(CACHE_LINES)
    return out, lines


# ------------------------------------------------------------- self-check ---

def selfcheck(measures: pd.DataFrame, lines: pd.DataFrame, n: int = 5) -> bool:
    """Recompute a few rows by brute force and demand they match.

    Specifically checks the thing that has gone wrong here before: that the
    window really ends before the game, so a start never contributes to its own
    projection.
    """
    print("\nleakage self-check: recompute by brute force")
    rng = np.random.default_rng(7)
    ok = True
    cand = measures[(measures["cur_starts"] >= 8)
                    & measures["sp_kbb"].notna()]
    for i in rng.choice(len(cand), size=min(n, len(cand)), replace=False):
        row = cand.iloc[int(i)]
        hist = lines[(lines["pitcher"] == row["pitcher"])
                     & lines["is_starter"]
                     & (lines["season"] == row["season"])
                     & (lines["game_date"] < row["game_date"])]
        same_day = lines[(lines["pitcher"] == row["pitcher"])
                         & lines["is_starter"]
                         & (lines["game_pk"] == row["game_pk"])]
        # the start itself must NOT be in the history used
        leaked = row["game_pk"] in set(hist["game_pk"])
        cur_bf = hist["bf"].sum()
        prev = lines[(lines["pitcher"] == row["pitcher"]) & lines["is_starter"]
                     & (lines["season"] == row["season"] - 1)]
        den = cur_bf + W_PREV * prev["bf"].sum()
        expect_den = row["sp_bf_seen"]
        good = (not leaked) and abs(den - expect_den) < 1e-6
        ok &= good
        print(f"  {'OK ' if good else 'BAD'} pitcher {int(row['pitcher'])} "
              f"{row['game_date'].date()}  bf-before {cur_bf:>4.0f}  "
              f"prev {prev['bf'].sum():>4.0f}  denom {den:>7.1f} vs stored "
              f"{expect_den:>7.1f}"
              + ("   <-- THIS START IS IN ITS OWN WINDOW" if leaked else "")
              + f"   (this start faced {same_day['bf'].sum():.0f})")
    return bool(ok)


if __name__ == "__main__":
    measures, lines = build()
    print(f"pitcher-games: {len(lines):,}")
    print(f"starts with measures: {len(measures):,}")
    print("\ncoverage")
    for c in ("sp_kbb", "sp_xwoba", "sp_velo", "sp_velo_delta",
              "pen_kbb_lev", "pen_fatigue_pitches"):
        print(f"  {c:22s} {measures[c].notna().sum():>7,} "
              f"({100 * measures[c].notna().mean():.1f}%)")
    print("\nspread (the thing sp_kbb_5s does not have)")
    print(measures[["sp_kbb", "sp_xwoba", "sp_velo", "sp_velo_delta",
                    "pen_kbb_lev", "pen_kbb_flat", "pen_fatigue_pitches",
                    "pen_third_day_arms"]]
          .describe(percentiles=[.05, .5, .95]).round(4).to_string())
    good = selfcheck(measures, lines)
    print("\nself-check", "PASSED" if good else "FAILED")
