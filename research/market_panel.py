"""One row per game of what the market said, and one row per price we ever saw.

    python research/market_panel.py          # summary + coverage

Everything in docs/experiments.md Part A and Part E reads its prices from here,
so the de-vigging happens in exactly one place. That matters more than it
sounds: A1 compares the model to the market, E3 compares the market to itself,
and E4 compares books to each other. If those three built their own "fair
probability" we would eventually find a difference between two books that was
really a difference between two code paths.

TWO TABLES.

  game_panel()   one row per finished MLB game: the de-vigged morning price and
                 closing price, per book and pooled, plus the outcome.

  book_long()    one row per (game, request, book): every price we hold, with
                 how many minutes before first pitch it was taken. E4 needs
                 this - "is DraftKings further off the sharp line 12 hours out
                 than 45 minutes out" is a question about the whole path, not
                 about the close.

WHAT "OPEN" AND "CLOSE" MEAN HERE.

  close   the latest request before first pitch. Median 45 min out. This is the
          number gate 1 and Phase 5 are judged on, and it is read from the
          market_close table rather than recomputed, so research and the gates
          can never drift apart.
  open    the 10:00 ET game-day request. Median ~8.8 h before first pitch.
          It is NOT a true opener - a real opener posts days earlier and we
          never captured one. Called `morning` in the columns for that reason;
          docs/experiments.md says the same.

The game -> odds-feed mapping is taken from market_close.odds_game_id. The
three feeds mint incompatible IDs and the (date, away, home) resolution in
bets/log.py:odds_twin is the validated way across them; market_close is that
resolution already run and stored, so reusing it means research cannot match
games differently than the gates do.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from db import connect
from bets.engine import american_to_prob

BOOKS = ["pinnacle", "draftkings", "fanduel", "betmgm"]
SHARP = "pinnacle"
SOFT = ["draftkings", "fanduel", "betmgm"]   # the three NJ books we hold


def _devig(away_ml, home_ml):
    """(p_away, p_home, overround). Vectorised twin of bets/engine.novig_probs.

    Returned alongside the overround because "is this gap bettable" is always
    the gap measured against that book's own vig on that game, never against a
    nominal -110.
    """
    pa = np.array([american_to_prob(x) if pd.notna(x) else np.nan
                   for x in away_ml], dtype=float)
    ph = np.array([american_to_prob(x) if pd.notna(x) else np.nan
                   for x in home_ml], dtype=float)
    tot = pa + ph
    return pa / tot, ph / tot, tot - 1.0


def book_long(con=None) -> pd.DataFrame:
    """Every de-vigged price we hold for a finished game, with its lead time."""
    own = con is None
    con = con or connect()
    q = """
      SELECT m.game_id, g.game_date, g.away, g.home, g.start_time_utc,
             g.home_score, g.away_score,
             s.ts, s.book, s.away_ml, s.home_ml, s.snapshot_type,
             s.commence_time
        FROM market_close m
        JOIN games g ON g.game_id = m.game_id
        JOIN odds_snapshots s ON s.game_id = m.odds_game_id
       WHERE s.away_ml IS NOT NULL AND s.home_ml IS NOT NULL
    """
    d = pd.read_sql(q, con)
    if own:
        con.close()

    # commence_time is the odds feed's first pitch; start_time_utc is the Stats
    # API's. Prefer the feed's, because the lead time being measured is "how
    # long before the game did THIS BOOK post THIS price", and the book was
    # working from its own clock. Fall back where the feed has none.
    start = pd.to_datetime(d["commence_time"], format="ISO8601", utc=True)
    start = start.fillna(pd.to_datetime(d["start_time_utc"], format="ISO8601",
                                        utc=True))
    taken = pd.to_datetime(d["ts"], format="ISO8601", utc=True)
    d["lead_min"] = (start - taken).dt.total_seconds() / 60.0

    # A price taken after first pitch is an in-play price. Those are not
    # forecasts of the same thing - the score is already in them - and grading
    # anything against one is the trap CLAUDE.md warns about.
    d = d[d["lead_min"] > 0].copy()

    pa, ph, vig = _devig(d["away_ml"], d["home_ml"])
    d["p_away"], d["p_home"], d["vig"] = pa, ph, vig
    d["season"] = pd.to_datetime(d["game_date"]).dt.year
    d["home_won"] = (d["home_score"] > d["away_score"]).astype(int)
    return d.reset_index(drop=True)


def game_panel(con=None) -> pd.DataFrame:
    """One row per game: morning and closing prices, per book and pooled."""
    own = con is None
    con = con or connect()
    mc = pd.read_sql("SELECT game_id, odds_game_id, snapshot_ts AS close_ts,"
                     " minutes_before_start AS close_lead, p_fair_home,"
                     " source AS close_source FROM market_close", con)
    long = book_long(con)
    if own:
        con.close()

    base = (long.groupby("game_id")
                .agg(game_date=("game_date", "first"), season=("season", "first"),
                     away=("away", "first"), home=("home", "first"),
                     home_won=("home_won", "first"))
                .reset_index())
    out = base.merge(mc, on="game_id", how="left")

    # --- the close: all books at the one request market_close resolved to ---
    at_close = long.merge(mc[["game_id", "close_ts"]], on="game_id")
    at_close = at_close[at_close["ts"] == at_close["close_ts"]]
    out = _spread_books(out, at_close, "close")

    # --- the morning: the EARLIEST game-day request we hold ---
    # Earliest, not latest: the question these prices answer is "what did the
    # market think before the day's news", so the further out the better. In
    # practice there is one per game, but taking the earliest is the behaviour
    # that stays right if a second morning pull is ever added.
    morn = long[long["snapshot_type"] == "hist_open"]
    first_ts = morn.groupby("game_id")["ts"].min().rename("morning_ts")
    morn = morn.merge(first_ts, on="game_id")
    morn = morn[morn["ts"] == morn["morning_ts"]]
    out = out.merge(
        morn.groupby("game_id")["lead_min"].first().rename("morning_lead"),
        on="game_id", how="left")
    out = _spread_books(out, morn, "morning")
    return out


def _spread_books(out: pd.DataFrame, rows: pd.DataFrame, tag: str):
    """Pivot per-book prices into columns, and add a pooled `fair` column."""
    for b in BOOKS:
        sub = rows[rows["book"] == b].groupby("game_id").agg(
            **{f"p_{b}_{tag}": ("p_home", "first"),
               f"vig_{b}_{tag}": ("vig", "first")}).reset_index()
        out = out.merge(sub, on="game_id", how="left")

    # "Fair" is Pinnacle where Pinnacle priced the game, otherwise the mean of
    # the de-vigged others - the same rule as bets/log.py:fair_prob, so the
    # research number and the gate number are the same number.
    soft_cols = [f"p_{b}_{tag}" for b in SOFT]
    consensus = out[soft_cols].mean(axis=1)
    pin = out[f"p_{SHARP}_{tag}"]
    out[f"p_fair_{tag}"] = pin.where(pin.notna(), consensus)
    out[f"fair_src_{tag}"] = np.where(pin.notna(), SHARP, "consensus")
    out.loc[out[f"p_fair_{tag}"].isna(), f"fair_src_{tag}"] = None
    return out


def _report(p: pd.DataFrame) -> None:
    print(f"games: {len(p):,}\n")
    print("coverage by season")
    print(f"{'season':>7s} {'games':>7s} {'close':>7s} {'morning':>8s} "
          f"{'both':>7s} {'pinn cl':>8s} {'pinn mo':>8s}")
    for s, g in p.groupby("season"):
        both = g["p_fair_close"].notna() & g["p_fair_morning"].notna()
        print(f"{s:>7d} {len(g):>7,} {g['p_fair_close'].notna().sum():>7,} "
              f"{g['p_fair_morning'].notna().sum():>8,} {both.sum():>7,} "
              f"{g['p_pinnacle_close'].notna().sum():>8,} "
              f"{g['p_pinnacle_morning'].notna().sum():>8,}")

    print("\nlead time, minutes before first pitch")
    for c in ("close_lead", "morning_lead"):
        q = p[c].describe(percentiles=[.05, .5, .95])
        print(f"  {c:13s} median {q['50%']:>7.0f}   5% {q['5%']:>7.0f}   "
              f"95% {q['95%']:>7.0f}")

    print("\nsanity: the closing price should be better calibrated than the "
          "morning one")
    both = p.dropna(subset=["p_fair_close", "p_fair_morning"])
    for tag in ("morning", "close"):
        q = both[f"p_fair_{tag}"].clip(1e-6, 1 - 1e-6)
        y = both["home_won"]
        ll = -(y * np.log(q) + (1 - y) * np.log(1 - q)).mean()
        print(f"  {tag:8s} log loss {ll:.6f}   n={len(both):,}")

    print("\nmean overround (the book's vig) at the close")
    for b in BOOKS:
        v = p[f"vig_{b}_close"]
        print(f"  {b:11s} {100 * v.mean():.2f}%   (n={v.notna().sum():,})")


if __name__ == "__main__":
    _report(game_panel())
