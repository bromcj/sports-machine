"""Paper trading: gate 2, run end to end and unattended.

    python run_daily.py paper      place today's, settle what finished, score it

Three steps, all free - they read the database and spend no API credits:

  place()   today's model picks, logged as mode='paper' at the best pregame
            price available. Uses allow_unvalidated=True, which is exactly
            what that flag is for: scoring a model that is not cleared.
  settle()  any paper bet whose game has finished AND has a closing price
            inside the window gets a result and a CLV.
  score()   hands the graded CLVs to validation.record_paper(), which decides
            whether gate 2 passes.

Why this has to exist: gate 2 requires 50+ graded paper bets at CLV that
clears the noise, and nothing in the pipeline was producing a single one.
The gate could never pass, which sounds safe and is not - an unreachable
gate is indistinguishable from a broken one, and the temptation is to
"fix" it by lowering it.

No paper bet can ever move money. record_bet(mode='paper') writes to the
same table as a real wager, deliberately - it is the same measurement - and
bets/engine.py still refuses every real bet until all three gates pass.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from db import connect
from bets.engine import evaluate, novig_probs
from bets.log import CLOSING_WINDOW_MIN, closing_snapshot, grade, record_bet
from model.predict import _odds_for
from model.validation import record_paper

PAPER_BANKROLL = 1000.0        # notional; only the CLV matters for gate 2


def place(date: str | None = None, sport: str = "mlb") -> int:
    """Log today's qualifying picks as paper bets. Returns how many were placed.

    One paper bet per game, ever. Re-running is safe and adds nothing - the
    morning run and a manual run on the same day must not produce two bets on
    the same game, or gate 2's count inflates without new evidence.
    """
    import datetime as dt
    date = date or dt.date.today().isoformat()
    con = connect()
    preds = con.execute(
        "SELECT p.game_id, p.home_win_prob, p.model_version, g.away, g.home"
        " FROM predictions p JOIN games g ON g.game_id = p.game_id"
        " WHERE p.sport=? AND g.game_date=?", (sport, date)).fetchall()

    placed = 0
    for p in preds:
        if con.execute("SELECT 1 FROM bets WHERE game_id=? AND mode='paper'",
                       (p["game_id"],)).fetchone():
            continue
        books, note = _odds_for(con, date, p["away"], p["home"])
        if note:
            continue
        # Take the best available price on whichever side the model likes,
        # which is what a real bettor shopping four books would do.
        best = None
        for b in books:
            r = evaluate(sport, p["home_win_prob"], b["away_ml"], b["home_ml"],
                         PAPER_BANKROLL, allow_unvalidated=True)
            if not r["bet"]:
                continue
            if best is None or r["edge"] > best[0]["edge"]:
                best = (r, b)
        if best is None:
            continue
        r, b = best
        record_bet(p["game_id"], sport, r["side"], b["book"], r["line"],
                   r["stake"], r["model_prob"], r["novig_market_prob"],
                   r["edge"], r["kelly_fraction"], p["model_version"],
                   mode="paper")
        placed += 1
        print(f"  paper: {p['away'][:18]} @ {p['home'][:18]}  "
              f"{r['side']} {r['line']:+d} @{b['book']}  edge {r['edge']:+.1%}")
    con.close()
    return placed


def settle(sport: str = "mlb") -> dict:
    """Grade paper bets whose game has finished. Returns a tally of outcomes."""
    con = connect()
    open_bets = con.execute(
        "SELECT * FROM bets WHERE mode='paper' AND sport=? AND result IS NULL",
        (sport,)).fetchall()
    tally = {"graded": 0, "no_result": 0, "no_close": 0, "settled_no_clv": 0}
    for b in open_bets:
        g = con.execute(
            "SELECT away_score, home_score, status FROM games WHERE game_id=?",
            (b["game_id"],)).fetchone()
        if g is None or g["away_score"] is None or g["status"] != "final":
            tally["no_result"] += 1
            continue
        home_won = g["home_score"] > g["away_score"]
        won = home_won if b["side"] == "home" else not home_won

        # Same book the bet was taken at. A close from a different book is a
        # different market's opinion, and CLV is a claim about beating the
        # price YOU got.
        snap = closing_snapshot(b["game_id"], book=b["book"])
        close_ml = None
        if snap is not None:
            close_ml = snap["home_ml"] if b["side"] == "home" else snap["away_ml"]
        usable = snap is not None and snap["is_closing"] and close_ml is not None

        if usable:
            grade(b["bet_id"], int(close_ml), won,
                  minutes_before_start=snap["minutes_before_start"])
            tally["graded"] += 1
        else:
            # Settle for P&L, but pass no closing time, so grade() records no
            # CLV and this bet cannot count toward gate 2.
            grade(b["bet_id"], None, won, minutes_before_start=None)
            tally["no_close" if snap is not None else "settled_no_clv"] += 1
    con.close()
    return tally


def score(sport: str = "mlb") -> dict | None:
    """Feed the graded paper CLVs into gate 2, with their first-pitch hours.

    The hours matter because which games get a gradeable close is decided by
    cron timing rather than at random - measured on this archive, every game
    that qualified started at 01:00 UTC. Gate 2 refuses a sample drawn from
    one start-time bucket, so it needs to be told the buckets.
    """
    con = connect()
    rows = con.execute(
        "SELECT bet_id, game_id, clv_pct FROM bets WHERE mode='paper' AND sport=?"
        " AND clv_pct IS NOT NULL", (sport,)).fetchall()
    con.close()
    if not rows:
        print("  no paper bets with a usable closing line yet - gate 2 untouched")
        return None
    clvs, hours = [], []
    for r in rows:
        snap = closing_snapshot(r["game_id"])
        ct = snap["commence_time"] if snap else None
        if not ct or len(ct) < 14:
            continue                    # cannot place it in a bucket; drop it
        clvs.append(r["clv_pct"])
        hours.append(int(ct[11:13]))
    if not clvs:
        print("  graded bets exist but none carry a first-pitch time - "
              "gate 2 untouched")
        return None
    return record_paper(sport, clvs, start_hours=hours)


def decompose(sport: str = "mlb") -> dict | None:
    """Split measured CLV into line-shopping and market-movement components.

    place() takes the BEST price across four books. The maximum of four noisy
    prices is biased upward, and if outlier books regress toward consensus then
    shopping alone produces positive CLV - which would let a model with no
    forecasting skill pass gate 2 on shopping skill.

    Measured on the archive as it stands: best-book CLV +0.344% (SE 0.557,
    t=0.62) against a randomly chosen book's -0.138%. n=32. That settles
    nothing in either direction - separating a gap that size from noise needs
    roughly 340 paired games.

    So this is a diagnostic, not a gate. It needs no new columns: everything
    it uses is already in odds_snapshots. Run it once there is enough data,
    BEFORE trusting gate 2, because gate 2 currently measures forecasting and
    shopping together and cannot tell you which one earned the CLV.
    """
    con = connect()
    rows = con.execute(
        "SELECT bet_id, game_id, side, book, line_taken, clv_pct FROM bets"
        " WHERE mode='paper' AND sport=? AND clv_pct IS NOT NULL",
        (sport,)).fetchall()
    if not rows:
        con.close()
        return None
    shop = []
    for r in rows:
        snap = closing_snapshot(r["game_id"])
        if snap is None:
            continue
        peers = con.execute(
            "SELECT away_ml, home_ml FROM odds_snapshots WHERE game_id=?"
            " AND ts=? AND away_ml IS NOT NULL", (snap["game_id"], snap["ts"])
        ).fetchall()
        prices = [(p["home_ml"] if r["side"] == "home" else p["away_ml"])
                  for p in peers]
        prices = [p for p in prices if p is not None]
        if len(prices) < 2:
            continue
        # How much better was the price taken than the average book's?
        shop.append(r["line_taken"] - sum(prices) / len(prices))
    con.close()
    if not shop:
        return None
    return {"n": len(shop), "mean_shopping_premium": sum(shop) / len(shop)}


def run(sport: str = "mlb", date: str | None = None):
    print(f"Paper trading [{sport}]")
    n = place(date, sport)
    print(f"  placed {n} new paper bet(s)")
    t = settle(sport)
    print(f"  settled: {t['graded']} with CLV, "
          f"{t['no_close'] + t['settled_no_clv']} without a usable close, "
          f"{t['no_result']} still awaiting a final")
    if t["no_close"] or t["settled_no_clv"]:
        print(f"    (a close must be within {CLOSING_WINDOW_MIN} min of first "
              f"pitch, at the same book - see bets/log.py)")
    r = score(sport)
    if r:
        print(f"  gate 2: {'PASS' if r['passed'] else 'not yet'} - {r['reason']}")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "mlb")
