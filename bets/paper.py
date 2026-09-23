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
from bets.engine import american_to_decimal
from bets.log import (CLOSING_WINDOW_MIN, closing_snapshot, fair_prob,
                      grade, record_bet)
from feeds import parse_utc, pregame_books
from model.validation import record_paper

PAPER_BANKROLL = 1000.0        # notional; only the CLV matters for gate 2

# How close to first pitch a paper bet may still be placed.
#
# place() used to have no such check, and the 10pm local job would happily bet
# on games already in the 5th inning. Worse, _odds_for returns the latest
# PREGAME price, which for a started game is the same snapshot settle() then
# uses as the close - so the CLV is exactly 0 by construction and the bet
# padded the 50-bet floor while carrying no information at all.
PLACE_CUTOFF_MIN = 10


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
        "SELECT p.game_id, p.home_win_prob, p.model_version, g.away, g.home,"
        " g.start_time_utc, g.status"
        " FROM predictions p JOIN games g ON g.game_id = p.game_id"
        " WHERE p.sport=? AND g.game_date=?", (sport, date)).fetchall()

    now = dt.datetime.now(dt.timezone.utc)
    placed = skipped_started = 0
    for p in preds:
        if con.execute("SELECT 1 FROM bets WHERE game_id=? AND mode='paper'",
                       (p["game_id"],)).fetchone():
            continue
        # A bet nobody could have placed is not evidence of anything.
        start = parse_utc(p["start_time_utc"])
        if start is None or p["status"] != "scheduled" or                 (start - now).total_seconds() / 60 < PLACE_CUTOFF_MIN:
            skipped_started += 1
            continue
        # By game_id, not by (date, teams). place() already knows which game
        # this is; re-deriving it from a date was the last remnant of the
        # matching that gave late games the previous night's prices.
        books, note = pregame_books(con, p["game_id"])
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
        # The exact price row this bet was taken at, so "could this bet have
        # been placed?" is answerable later instead of inferred.
        taken = parse_utc(b["ts"])
        if taken is None or not (taken <= now < start):
            skipped_started += 1
            continue
        bet_id = record_bet(p["game_id"], sport, r["side"], b["book"], r["line"],
                            r["stake"], r["model_prob"], r["novig_market_prob"],
                            r["edge"], r["kelly_fraction"], p["model_version"],
                            mode="paper")
        con.execute("UPDATE bets SET odds_snapshot_id=? WHERE bet_id=?",
                    (b["id"], bet_id))
        con.commit()
        placed += 1
        print(f"  paper: {p['away'][:18]} @ {p['home'][:18]}  "
              f"{r['side']} {r['line']:+d} @{b['book']}  edge {r['edge']:+.1%}")
    con.close()
    if skipped_started:
        print(f"  skipped {skipped_started} game(s): already started, not "
              f"scheduled, or within {PLACE_CUTOFF_MIN} min of first pitch")
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
            _decompose_bet(con, b, snap)
            tally["graded"] += 1
        else:
            # Settle for P&L, but pass no closing time, so grade() records no
            # CLV and this bet cannot count toward gate 2.
            grade(b["bet_id"], None, won, minutes_before_start=None)
            tally["no_close" if snap is not None else "settled_no_clv"] += 1
    con.close()
    return tally


def _decompose_bet(con, bet, close_snap) -> bool:
    """Split this bet into line-shopping value and model-information value.

    clv_pct compares the price taken to the same book's CLOSING price with the
    vig still in it. That is not expected value - a bet can beat it and still
    lose money - and it cannot tell a good price from a good forecast. place()
    takes the BEST of four books, and outlier prices regress toward consensus,
    so shopping alone produces positive CLV. A model with no forecasting skill
    could pass gate 2 on shopping skill.

    Against the FAIR (de-vigged) line, the two separate exactly:

        shop = decimal_taken * p_fair_at_bet - 1
            how good the price was against the fair line AT THE MOMENT OF THE
            BET. Real money, but it is not evidence the model forecasts
            anything.

        info = p_fair_close / p_fair_at_bet - 1
            did the fair line move toward the side the model picked. This does
            not depend on which book was shopped, so a zero-skill model has an
            expected value of about 0 here however good the shopping.

        (1 + shop) * (1 + info) = 1 + ev_fair_close

    Gate 2 tests `info`. shop and ev are recorded beside it for reporting.
    """
    snap_id = bet["odds_snapshot_id"]
    if snap_id is None:
        return False
    at = con.execute("SELECT game_id, ts FROM odds_snapshots WHERE id=?",
                     (snap_id,)).fetchone()
    if at is None:
        return False
    p_bet, src_bet = fair_prob(con, at["game_id"], at["ts"], bet["side"])
    p_close, src_close = fair_prob(con, close_snap["game_id"],
                                   close_snap["ts"], bet["side"])
    if not p_bet or not p_close:
        return False
    dec = american_to_decimal(bet["line_taken"])
    shop = dec * p_bet - 1
    info = p_close / p_bet - 1
    ev = dec * p_close - 1
    con.execute(
        "UPDATE bets SET ev_fair_close=?, shop_pct=?, info_pct=?, fair_source=?"
        " WHERE bet_id=?",
        (round(ev * 100, 4), round(shop * 100, 4), round(info * 100, 4),
         src_close if src_close == src_bet else f"{src_bet}->{src_close}",
         bet["bet_id"]))
    con.commit()
    return True


def score(sport: str = "mlb") -> dict | None:
    """Feed the graded paper CLVs into gate 2, with their first-pitch hours.

    The hours matter because which games get a gradeable close is decided by
    cron timing rather than at random - measured on this archive, every game
    that qualified started at 01:00 UTC. Gate 2 refuses a sample drawn from
    one start-time bucket, so it needs to be told the buckets.
    """
    con = connect()
    rows = con.execute(
        "SELECT bet_id, game_id, info_pct, shop_pct, ev_fair_close FROM bets"
        " WHERE mode='paper' AND sport=? AND info_pct IS NOT NULL",
        (sport,)).fetchall()
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
        # info, not clv_pct: the part of the result the model earned.
        clvs.append(r["info_pct"])
        hours.append(int(ct[11:13]))
    if not clvs:
        print("  graded bets exist but none carry a first-pitch time - "
              "gate 2 untouched")
        return None
    return record_paper(sport, clvs, start_hours=hours)


def summary(sport: str = "mlb") -> dict | None:
    """Mean shop / info / ev across graded paper bets, for reporting.

    Replaces an earlier decompose() that measured the wrong thing twice over:
    it averaged AMERICAN odds across books, which is meaningless across the
    +/-100 boundary (taking +104 against -105/+100/-102/+104 reported a
    "premium" of 104.75 where the real advantage is about 2.3%), and it
    compared against peers at the CLOSING snapshot rather than the one the bet
    was placed from, mixing line movement into "shopping".
    """
    con = connect()
    rows = con.execute(
        "SELECT shop_pct, info_pct, ev_fair_close, fair_source FROM bets"
        " WHERE mode='paper' AND sport=? AND info_pct IS NOT NULL",
        (sport,)).fetchall()
    con.close()
    if not rows:
        return None
    n = len(rows)
    return {"n": n,
            "shop": sum(r["shop_pct"] for r in rows) / n,
            "info": sum(r["info_pct"] for r in rows) / n,
            "ev": sum(r["ev_fair_close"] for r in rows) / n,
            "sources": sorted({r["fair_source"] for r in rows})}


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
