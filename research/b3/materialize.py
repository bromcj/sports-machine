"""Turn the raw prop responses into one scoreable table. Free, offline.

    python props/materialize.py

Reads data/props/raw/**/*.json.gz and writes data/props_nfl.parquet, one row
per (game, book, market, player, line) with the over and under prices paired
and de-vigged.

WHY PAIRED AND NOT ONE ROW PER PRICE. A book's over price is not a probability
on its own - it carries the margin, and the margin only cancels against the
matching under. Storing the two sides on one row makes it impossible to de-vig
one without the other, which is the shape of mistake that produces a
"probability" that is really a price.

WHY THE LINE IS PART OF THE KEY. A book can post the same player twice at
different numbers - 4.5 and 5.5 receptions - and those are different bets.
Keying on (player, book, market) alone would collapse them and silently pair an
over at one line with an under at another.

FAIR PRICE. Pinnacle where Pinnacle priced that exact player and line,
otherwise the de-vigged consensus of the others, and the row records which -
the same rule as bets/log.py:fair_prob, deliberately, so a prop result and a
moneyline result are measured with one ruler. B2 found Pinnacle does price NFL
props, which is why this anchor exists at all; it does not price every player,
so the consensus fallback is not theoretical.
"""
import gzip
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from bets.engine import american_to_prob
from research.b3.backfill_nfl_props import RAW_DIR, PARQUET, load_progress

SHARP = "pinnacle"


def _rows():
    prog = load_progress()
    # event_id -> our game id, so the prices can be joined back to a schedule
    by_event = {r["event_id"]: k.split(":", 1)[1]
                for k, r in prog.items()
                if r.get("ok") and r.get("event_id")}

    for f in sorted(RAW_DIR.glob("**/*.json.gz")):
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            payload = json.load(fh)
        data = payload.get("data", {})
        eid = data.get("id") or f.stem
        gid = by_event.get(eid)
        for bk in data.get("bookmakers", []):
            for mkt in bk.get("markets", []):
                for o in mkt.get("outcomes", []):
                    yield {
                        "game_id": gid,
                        "event_id": eid,
                        "snapshot_ts": payload.get("timestamp"),
                        "commence_time": data.get("commence_time"),
                        "home_team": data.get("home_team"),
                        "away_team": data.get("away_team"),
                        "book": bk.get("key"),
                        "market": mkt.get("key"),
                        "player": o.get("description"),
                        "side": o.get("name"),
                        "line": o.get("point"),
                        "price": o.get("price"),
                    }


def build() -> pd.DataFrame:
    long = pd.DataFrame(_rows())
    if long.empty:
        raise SystemExit(f"no raw responses under {RAW_DIR}")
    print(f"{len(long):,} raw prices from "
          f"{long['event_id'].nunique():,} events")

    long = long.dropna(subset=["player", "line", "price", "side"])
    key = ["game_id", "event_id", "snapshot_ts", "commence_time", "home_team",
           "away_team", "book", "market", "player", "line"]
    wide = (long.pivot_table(index=key, columns="side", values="price",
                             aggfunc="first").reset_index())
    wide.columns.name = None
    for c in ("Over", "Under"):
        if c not in wide.columns:
            wide[c] = np.nan
    wide = wide.rename(columns={"Over": "over_price", "Under": "under_price"})

    both = wide["over_price"].notna() & wide["under_price"].notna()
    print(f"{len(wide):,} (game, book, market, player, line) rows; "
          f"{both.sum():,} have both sides")
    # A one-sided quote cannot be de-vigged, so it cannot become a probability.
    # Kept in the table with NaN rather than dropped, because "this book only
    # showed one side" is itself information about liquidity.
    pa = wide["over_price"].where(both).map(
        lambda x: american_to_prob(x) if pd.notna(x) else np.nan)
    pb = wide["under_price"].where(both).map(
        lambda x: american_to_prob(x) if pd.notna(x) else np.nan)
    total = pa + pb
    wide["p_over_book"] = pa / total
    wide["vig"] = total - 1.0

    # --- the fair price, per (game, market, player, line) ---
    grp = ["game_id", "market", "player", "line"]
    pin = (wide[wide["book"] == SHARP][grp + ["p_over_book"]]
           .rename(columns={"p_over_book": "p_fair_sharp"}))
    cons = (wide[wide["book"] != SHARP].groupby(grp)["p_over_book"]
            .mean().rename("p_fair_consensus").reset_index())
    out = wide.merge(pin, on=grp, how="left").merge(cons, on=grp, how="left")
    out["p_fair"] = out["p_fair_sharp"].where(out["p_fair_sharp"].notna(),
                                              out["p_fair_consensus"])
    out["fair_source"] = np.where(out["p_fair_sharp"].notna(), SHARP,
                                  "consensus")
    out.loc[out["p_fair"].isna(), "fair_source"] = None
    return out


def report(d: pd.DataFrame) -> None:
    print(f"\ngames: {d['game_id'].nunique():,}   "
          f"players: {d['player'].nunique():,}")
    print("\nrows per market")
    print(d.groupby("market").size().to_string())
    print("\nbooks")
    print(d.groupby("book").agg(rows=("over_price", "size"),
                                both_sides=("p_over_book", "count"),
                                mean_vig=("vig", "mean")).round(4).to_string())
    print("\nfair-price source (the thing that must be recorded per row)")
    print(d["fair_source"].value_counts(dropna=False).to_string())
    print("\nPinnacle coverage by market - the anchor is not universal")
    pin = d[d["book"] == SHARP]
    for m, g in d.groupby("market"):
        have = pin[pin["market"] == m]["game_id"].nunique()
        print(f"  {m:24s} {have:,} of {g['game_id'].nunique():,} games")
    print("\nplayers priced per game")
    per = d.groupby(["game_id", "market"])["player"].nunique()
    print(per.groupby("market").describe(
        percentiles=[.1, .5, .9]).round(1).to_string())


if __name__ == "__main__":
    d = build()
    PARQUET.parent.mkdir(parents=True, exist_ok=True)
    d.to_parquet(PARQUET, index=False)
    report(d)
    print(f"\nwrote {len(d):,} rows -> {PARQUET}")
