"""Assemble the training table from backfilled data and run walk-forward CV.

  python features/build_training.py

Joins, per historical final game:
  - bullpen quality/fatigue and offense on (team, game_date); both tables are
    already shift(1)-ed at source, so a plain keyed merge is leakage-safe
  - the actual starter's shifted 5-start form via (game_pk, team)
  - rest days from the schedule
  - park factor for the home VENUE that season, computed from prior seasons

Every join is by explicit key. Never assign a merge result back positionally
(`.values`): pandas' default sort is not stable, so re-sorting a date-sorted
frame reshuffles same-day games and silently pairs each team with another
team's numbers.

Baseline: each test season is scored against the REAL de-vigged closing
price in market_close (bought historical odds, resolved by
resolve_market_close.py - Pinnacle where it priced the game). A season counts
as real when at least half its games have a close; inside such a season a game
with no close is dropped rather than compared against a stand-in.

Seasons with no bought closes (2022 and 2023, which are only ever training
seasons) fall back to the home win rate of the seasons BEFORE them -
leakage-free, because a season never contributes to its own baseline. That
fallback is a placeholder: if any TEST season needed it, the run is recorded
as PLACEHOLDER and clears nothing. (It used to be a flat 0.54, which was
simply wrong: the actual rate over this window is 52.3-52.9%.)
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import paths
from db import connect
from features.sports.mlb_features import (
    load_statcast, pitcher_game_lines, bullpen_table, offense_table,
    starter_table, park_factor_table, venue_id, venue_series, TEAM_ABBR)
from model.train import walk_forward
from model.validation import record, explain, PLACEHOLDER, REAL_MARKET
from model.persist import save as save_model, describe

# Fallback only, for the earliest season, which has no prior season to
# measure and is never scored anyway (walk_forward needs 2 training seasons).
HOME_BASELINE_PRIOR = 0.5
OUT = paths.training_table("mlb")


def load_games() -> pd.DataFrame:
    con = connect()
    g = pd.read_sql(
        # 'mlb-espn-401817028' also matches 'mlb-%'. Those rows come from the
        # ESPN scoreboard fallback and carry no gamePk, so they cannot join to
        # Statcast; excluding them here keeps game_pk parseable as an int.
        "SELECT * FROM games WHERE sport='mlb' AND status='final'"
        " AND game_id LIKE 'mlb-%' AND game_id NOT LIKE 'mlb-espn-%'", con)
    con.close()
    g["game_pk"] = g["game_id"].str.replace("mlb-", "").astype(int)
    g["game_date"] = pd.to_datetime(g["game_date"])
    g["home_ab"] = g["home"].map(TEAM_ABBR)
    g["away_ab"] = g["away"].map(TEAM_ABBR)
    g = g.dropna(subset=["home_ab", "away_ab", "home_score", "away_score"])
    g["run_diff"] = g["home_score"] - g["away_score"]
    g["home_won"] = (g["run_diff"] > 0).astype(int)
    g["season"] = g["game_date"].dt.year
    return g.sort_values("game_date").reset_index(drop=True)


def rest_days(df: pd.DataFrame, team_col: str) -> list:
    """Days since that team last played ANY game. df must be sorted by date.

    It used to track only `df[team_col]`, so rest_days(df, "home_ab") measured
    days since the team's last HOME game - a homestand-length feature, not a
    rest feature. features/build.py:rest_days_asof has always checked both
    columns, so training and serving were computing DIFFERENT features under
    the same name.

    Measured over 12,052 games: 16% of rows disagreed, mean 4.04 days against
    live's 2.03, and the maximum ran to 197 days because a team's first away
    game of a season is months after its last away game of the previous one.

    Found while fixing the off-season window problem, not by looking for it -
    the impossible rest values that survived the window mask are what exposed
    it.
    """
    last, out = {}, []
    for away, home, d in zip(df["away_ab"], df["home_ab"], df["game_date"]):
        team = away if team_col == "away_ab" else home
        out.append((d - last[team]).days if team in last else np.nan)
        last[away] = d
        last[home] = d
    return out


def assemble() -> pd.DataFrame:
    games = load_games()
    print(f"{len(games)} final games loaded.")
    sc = load_statcast()
    print(f"{len(sc):,} Statcast pitches loaded.")
    lines = pitcher_game_lines(sc)
    pen, off, sp = bullpen_table(lines), offense_table(sc), starter_table(lines)

    df = games.copy()
    n = len(df)
    for side in ("home", "away"):
        ab = f"{side}_ab"
        df = df.merge(
            pen.rename(columns={"team": ab,
                                "pen_kbb_30d": f"{side}_pen_kbb_30d",
                                "pen_pitches_3d": f"{side}_pen_pitches_3d"}),
            on=[ab, "game_date"], how="left")
        df = df.merge(
            off.rename(columns={"team": ab,
                                "off_woba_30d": f"{side}_off_woba_30d"}),
            on=[ab, "game_date"], how="left")
        df = df.merge(
            sp.rename(columns={"pitch_team": ab,
                               "sp_kbb_5s": f"{side}_sp_kbb_5s"}),
            on=["game_pk", ab], how="left")
    if len(df) != n:
        raise AssertionError(
            f"joins changed the row count ({n} -> {len(df)}); a stat table has "
            f"duplicate keys and is fanning games out.")

    df = df.sort_values("game_date", kind="stable").reset_index(drop=True)
    for side in ("home", "away"):
        df[f"{side}_rest_days"] = rest_days(df, f"{side}_ab")

    # Park factor is keyed on VENUE and season, not team: the Athletics have
    # played in two parks inside this window and a static team->park map would
    # hand their Sacramento games the Oakland Coliseum's number.
    pf = park_factor_table(games)
    # Same rule as the table itself: the venue the game was actually played
    # at, from the Stats API, falling back to the map only where unknown.
    vmap = dict(zip(games["game_id"], venue_series(games)))
    df["venue"] = [vmap.get(gid) or v for gid, v in
                   zip(df["game_id"], (venue_id(t, s) for t, s
                                       in zip(df["home_ab"], df["season"])))]
    df["park_factor"] = [pf[(v, s)] for v, s in zip(df["venue"], df["season"])]
    # The baseline each season is scored against.
    #
    # Where a REAL de-vigged closing price exists it is used. Where it does not
    # - 2022 and 2023, which were never bought, because walk_forward reads
    # novig_home_prob only for the TEST season - the fallback is the home win
    # rate of prior seasons, which is a placeholder and clears nothing.
    #
    # A season with real coverage requires it per game: a row with no close is
    # DROPPED rather than quietly compared against a constant, because mixing
    # the two inside one season would make the result mean nothing.
    seasons_sorted = sorted(df["season"].unique())
    prior_rate = {}
    for idx, yr in enumerate(seasons_sorted):
        earlier = df[df["season"].isin(seasons_sorted[:idx])]
        prior_rate[yr] = (float(earlier["home_won"].mean()) if len(earlier)
                          else HOME_BASELINE_PRIOR)

    con = connect()
    mc = {r["game_id"]: (r["p_fair_home"], r["source"])
          for r in con.execute("SELECT game_id, p_fair_home, source FROM market_close")}
    con.close()
    df["market_close"] = df["game_id"].map(lambda g: (mc.get(g) or (None,))[0])
    df["market_source"] = df["game_id"].map(lambda g: (mc.get(g) or (None, None))[1])

    covered = {}
    for yr in seasons_sorted:
        sub = df[df["season"] == yr]
        covered[yr] = float(sub["market_close"].notna().mean())
    REAL = 0.5
    real_seasons = [y for y in seasons_sorted if covered[y] >= REAL]
    print("Baseline per season:")
    for yr in seasons_sorted:
        if covered[yr] >= REAL:
            print(f"  {yr}: REAL de-vigged close, {covered[yr]:.1%} of games")
        else:
            print(f"  {yr}: placeholder {prior_rate[yr]:.3f} "
                  f"(home rate of prior seasons)")
    before = len(df)
    df = df[~(df["season"].isin(real_seasons) & df["market_close"].isna())]
    if before - len(df):
        print(f"  dropped {before - len(df)} game(s) in a real-market season "
              f"with no closing price - never compared against a placeholder")
    df["novig_home_prob"] = [
        m if (s in real_seasons and m == m and m is not None) else prior_rate[s]
        for m, s in zip(df["market_close"], df["season"])]
    BASELINE_KIND = (REAL_MARKET
                     if set(seasons_sorted[2:]) <= set(real_seasons)
                     else PLACEHOLDER)
    print(f"  -> recording as {BASELINE_KIND.upper()}")
    globals()["_BASELINE_KIND"] = BASELINE_KIND

    feats = [c for c in df.columns if any(
        c.startswith(p) for p in ("home_pen", "away_pen", "home_off", "away_off",
                                  "home_sp", "away_sp", "home_rest", "away_rest"))]
    feats.append("park_factor")
    before = len(df)
    df = df.dropna(subset=feats)
    print(f"{len(df)} games with complete features "
          f"({before - len(df)} early-window games dropped).")
    df.to_parquet(OUT)
    return df, feats


if __name__ == "__main__":
    df, feats = assemble()
    print(f"\nFeatures: {feats}\nTraining table saved -> {OUT.name}\n")
    if df["season"].nunique() >= 3:
        # intercept=True: D1 measured the home intercept against the REAL
        # de-vigged closing line and it improved log loss in all three test
        # seasons (-0.00090, -0.00282, -0.00125; pooled +0.001662, t = +2.98,
        # day-block bootstrap). See research/d1_calibration.py. The bar in
        # docs/briefs/2026-09-23-next-task.md (Part D) was "helps in every
        # season", not on average.
        # logloss_model_noint stays in the output so that bar is re-checked on
        # every run instead of being taken on trust.
        results = walk_forward(df, feats, target_col="run_diff",
                               season_col="season", sport="mlb",
                               intercept=True)
        print(results.to_string(index=False))
        # Recorded as REAL_MARKET when every test season has real closes
        # (assemble() decides, and prints which), otherwise PLACEHOLDER - and
        # a placeholder clears nothing however well this scores, which is
        # correct: beating a constant is not evidence of edge.
        record("mlb", globals().get("_BASELINE_KIND", PLACEHOLDER),
               results.rename(columns={"test_season": "season"}).to_dict("records"))
        print()
        print(explain("mlb"))
        # Fit one final model on EVERY season and save it, so the daily run
        # can score games that have not been played. The alpha is the one the
        # most recent fold chose; k stays the config prior (k_fit says the
        # data has no better answer - see model/train.py).
        alpha = float(results.iloc[-1]["alpha"])
        # The intercept the most recent fold fitted, carried into the saved
        # bundle so served predictions match the ones walk_forward scored.
        # Training and serving computing different probabilities under the same
        # name is the exact shape of the rest_days bug.
        a_fit = float(results.iloc[-1]["a_used"])
        path = save_model("mlb", df, feats, "run_diff", alpha, a=a_fit)
        # Not relative_to(ROOT): with SPORTS_MACHINE_DATA_DIR set, the model
        # lives outside this checkout entirely.
        try:
            shown = path.relative_to(ROOT)
        except ValueError:
            shown = path
        print(f"Saved model -> {shown}")
        print("  " + describe("mlb"))
        print()
        print("Bar: logloss_model < logloss_market on REAL de-vigged closing"
              " lines. The archive is collecting them now.")
    else:
        print("Need 3+ seasons backfilled for walk-forward CV. "
              "Run: python backfill.py")
