"""Resolve the de-vigged closing price for every finished game. Free.

    python resolve_market_close.py

Walks each game to its odds-feed twin, takes the latest snapshot BEFORE first
pitch, and de-vigs it - Pinnacle where it priced the game, the consensus of
the other books otherwise, recording which.

Materialised into market_close because the walk is per-game and far too slow
to repeat inside every training run. It also means the number gate 1 is judged
on can be inspected directly, instead of being recomputed slightly differently
by each reader.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
from db import connect, utc_now
from feeds import SQL_STATS_API
from bets.log import closing_snapshot, fair_prob


def resolve(seasons=None, rebuild=False) -> dict:
    con = connect()
    if rebuild:
        con.execute("DELETE FROM market_close")
    q = (f"SELECT game_id, game_date FROM games WHERE sport='mlb'"
         f" AND ({SQL_STATS_API}) AND status='final' AND away_score IS NOT NULL")
    args = []
    if seasons:
        q += " AND substr(game_date,1,4) IN (" + ",".join("?" * len(seasons)) + ")"
        args = list(seasons)
    if not rebuild:
        q += " AND game_id NOT IN (SELECT game_id FROM market_close)"
    rows = con.execute(q, args).fetchall()

    tally = {"resolved": 0, "no_close": 0, "no_price": 0}
    for r in rows:
        snap = closing_snapshot(r["game_id"])
        if snap is None:
            tally["no_close"] += 1
            continue
        p, src = fair_prob(con, snap["game_id"], snap["ts"], "home")
        if not p:
            tally["no_price"] += 1
            continue
        con.execute(
            "INSERT OR REPLACE INTO market_close (game_id, odds_game_id,"
            " snapshot_ts, minutes_before_start, p_fair_home, source,"
            " resolved_at) VALUES (?,?,?,?,?,?,?)",
            (r["game_id"], snap["game_id"], snap["ts"],
             snap["minutes_before_start"], float(p), src, utc_now()))
        tally["resolved"] += 1
    con.commit()
    con.close()
    return tally


if __name__ == "__main__":
    t = resolve(rebuild="--rebuild" in sys.argv)
    print(f"resolved {t['resolved']:,}  (no close {t['no_close']}, "
          f"no usable price {t['no_price']})")
