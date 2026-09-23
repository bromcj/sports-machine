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
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from db import LATEST_PREDICTION, connect
from bets.engine import evaluate, novig_probs
from bets.guardrails import daily_exposure, flags_for_game
from bets.engine import american_to_decimal
from bets.log import (CLOSING_WINDOW_MIN, closing_snapshot, fair_prob,
                      grade, record_bet)
from feeds import ET, parse_utc, pregame_books
from model.validation import record_paper, slot_of

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
        f"SELECT p.prediction_id, p.game_id, p.home_win_prob, p.model_version,"
        f" g.away, g.home, g.start_time_utc, g.status"
        f" FROM ({LATEST_PREDICTION}) p JOIN games g ON g.game_id = p.game_id"
        f" WHERE p.sport=? AND g.game_date=?", (sport, date)).fetchall()

    now = dt.datetime.now(dt.timezone.utc)
    placed = skipped_started = 0
    for p in preds:
        if con.execute("SELECT 1 FROM bets WHERE game_id=? AND mode IN"
                       " ('paper','placebo')", (p["game_id"],)).fetchone():
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
        # Guardrails can finally fire: no caller ever passed flags, so the
        # protections evaluate() advertises had never once been applied.
        best = None
        gflags = flags_for_game(con, p["game_id"], "home")
        aflags = flags_for_game(con, p["game_id"], "away")
        for b in books:
            side_guess = ("home" if p["home_win_prob"] >= 0.5 else "away")
            r = evaluate(sport, p["home_win_prob"], b["away_ml"], b["home_ml"],
                         PAPER_BANKROLL, allow_unvalidated=True,
                         flags=(gflags if side_guess == "home" else aflags),
                         exposure_used=daily_exposure(con, sport, date,
                                                      PAPER_BANKROLL))
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
        # Shadow bet with a RANDOM side, same game, book and snapshot. Seeded
        # from the game_id so it is reproducible and cannot be reshuffled after
        # the fact. If this clears gate 2 too, the gate is measuring something
        # other than the model.
        coin = int(hashlib.sha256(p["game_id"].encode()).hexdigest(), 16) & 1
        pl_side = "home" if coin else "away"
        pl_line = b["home_ml"] if pl_side == "home" else b["away_ml"]

        # record_bet opens its OWN connection, so `con` must not be holding an
        # uncommitted write while it runs - that deadlocks SQLite against
        # itself. Both inserts first, then the updates together.
        new_ids = [(record_bet(p["game_id"], sport, r["side"], b["book"],
                               r["line"], r["stake"], r["model_prob"],
                               r["novig_market_prob"], r["edge"],
                               r["kelly_fraction"], p["model_version"],
                               mode="paper"))]
        if pl_line is not None:
            new_ids.append(record_bet(p["game_id"], sport, pl_side, b["book"],
                                      pl_line, r["stake"], 0.5, 0.5, 0.0,
                                      r["kelly_fraction"], p["model_version"],
                                      mode="placebo"))
        for bid in new_ids:
            # Which price AND which prediction. Two predictions a day were
            # possible and nothing recorded which one a bet acted on.
            con.execute("UPDATE bets SET odds_snapshot_id=?, prediction_id=?"
                        " WHERE bet_id=?", (b["id"], p["prediction_id"], bid))
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
        "SELECT * FROM bets WHERE mode IN ('paper','placebo') AND sport=?"
        " AND result IS NULL", (sport,)).fetchall()
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


def _graded(con, sport: str, mode: str):
    """(info values, slot labels) for graded bets in that mode."""
    rows = con.execute(
        "SELECT game_id, info_pct FROM bets WHERE mode=? AND sport=?"
        " AND info_pct IS NOT NULL", (mode, sport)).fetchall()
    vals, slots = [], []
    for r in rows:
        snap = closing_snapshot(r["game_id"])
        start = parse_utc(snap["commence_time"]) if snap else None
        if start is None:
            continue
        vals.append(r["info_pct"])
        slots.append(slot_of(start.astimezone(ET).hour))
    return vals, slots


def score(sport: str = "mlb") -> dict | None:
    """Feed the graded paper `info` values into gate 2, with what they need.

    Three things travel with them, and gate 2 fails closed without each:

      slots      the ET slate slot of each graded bet, so a sample that is
                 all late west-coast games is visible rather than averaged.
      coverage   graded / settled. Which games get a gradeable close is
                 decided by cron timing, not at random, so this is the honest
                 measure of whether the graded set represents the bets placed.
      placebo    the same pipeline with a random side. If that clears the gate
                 too, the gate is measuring something other than the model.
    """
    con = connect()
    clvs, slots = _graded(con, sport, "paper")
    placebo, _ = _graded(con, sport, "placebo")
    settled = con.execute(
        "SELECT COUNT(*) FROM bets WHERE mode='paper' AND sport=?"
        " AND result IS NOT NULL", (sport,)).fetchone()[0]
    con.close()
    if not clvs:
        print("  no paper bets with a gradeable closing line yet - "
              "gate 2 untouched")
        return None
    coverage = (len(clvs) / settled) if settled else None
    return record_paper(sport, clvs, slots=slots, coverage=coverage,
                        placebo=placebo)


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
