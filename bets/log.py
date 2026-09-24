"""Bet log writes, closing-line grading, and the weekly review.

Kill criterion, printed by review() (it is a message, nothing reads it):
rolling 50-bet average CLV < 0 => STOP BETTING, diagnose.
"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
from db import connect, utc_now
from feeds import parse_utc
from bets.engine import clv_pct, american_to_decimal, novig_probs


def record_bet(game_id, sport, side, book, line_taken, stake, model_prob,
               novig_market_prob, edge, kelly_fraction, model_version,
               mode: str = "real"):
    """Log a wager. Returns its bet_id.

    `mode` defaults to 'real' deliberately. Gate 2 counts PAPER bets, so an
    unlabelled bet contributes nothing toward opening the tap - the mistake
    costs you a slower gate, not an unearned one. The reverse default would
    let anything that forgot the flag help justify staking money.

    'placebo' is the same pipeline with a RANDOM side. It exists to be
    measured, not to be believed: if the placebo also clears gate 2, the test
    is measuring something other than the model and gate 2 refuses.
    """
    if mode not in ("paper", "real", "placebo"):
        raise ValueError(
            f"mode must be 'paper', 'real' or 'placebo', got {mode!r}")
    con = connect()
    cur = con.execute(
        """INSERT INTO bets (mode, ts, game_id, sport, side, book, line_taken, stake,
                             model_prob, novig_market_prob, edge, kelly_fraction,
                             model_version)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (mode, utc_now(), game_id, sport, side, book, line_taken, stake,
         model_prob, novig_market_prob, edge, kelly_fraction, model_version))
    bet_id = cur.lastrowid
    con.commit()
    con.close()
    return bet_id


# How close to first pitch a snapshot must be to count as a CLOSING line.
#
# CLV only means anything against the price the market actually settled at.
# Measured on archive/: the nearest pregame MLB snapshot is a median of 6.1
# HOURS before first pitch, and only 4 of 29 games had one inside this window.
# Grading those as "closing line value" would be measuring drift over an
# afternoon and calling it edge.
#
# Snapshots outside the window are not errors - they are perfectly good prices,
# just not closing ones - so they are kept and reported, and simply do not
# count toward gate 2.
#
# KNOWN AND DELIBERATE, reviewed 2026-09-23: the current collection schedule
# cannot reliably meet this. Only 7 of 64 recent MLB games qualified. GitHub
# starts scheduled runs hours late, so the 5:09pm pull is either ~2h early (too
# soon) or lands in-play (rejected), and the queue decides which.
#
# Left as-is on purpose. It blocks nothing today - MLB cannot pass gate 1
# either, because its baseline is still a placeholder - and the standard is
# right even when the schedule cannot meet it.
#
# Do NOT widen this to make the schedule look adequate. That would make gate 2
# measure an afternoon of drift and call it closing-line value, which is the
# exact failure the gate exists to prevent. If the window has to change, the
# collection schedule is what changes: a locally scheduled pull ~45 min before
# first pitch hits it precisely, at no extra API cost, and was the runner-up
# option. Revisit when gate 1 is actually within reach.
CLOSING_WINDOW_MIN = 60


# Matching lives in feeds.py now: same teams, first pitch within a window,
# never date strings. Re-exported so callers do not all have to change.
from feeds import odds_twin           # noqa: E402,F401


def closing_snapshot(game_id: str, book: str | None = None):
    """The truest closing line we captured for one game: the latest snapshot
    taken BEFORE first pitch, whatever pull it happened to come from.

    Why not just trust snapshot_type='close'? A single pull returns tonight's
    games and games several days out, so the same label covers prices that are
    minutes from the close and prices that are days from it. commence_time is
    what separates them. Scheduled pulls are also queued by GitHub and can run
    late, so the intended close pull is not reliably the nearest one.

    Returns the row plus minutes_before_start, so you can judge how good an
    anchor it is - a snapshot 400 minutes early is not a closing line, and CLV
    computed against it is not meaningful.
    """
    con = connect()
    # The id we were handed may belong to a feed that carries no odds at all.
    if not con.execute("SELECT 1 FROM odds_snapshots WHERE game_id=? LIMIT 1",
                       (game_id,)).fetchone():
        twin, note = odds_twin(con, game_id)
        if twin is None:
            con.close()
            return None
        game_id = twin
    q = "SELECT * FROM odds_snapshots WHERE game_id=? AND commence_time IS NOT NULL"
    args = [game_id]
    if book:
        q += " AND book=?"
        args.append(book)
    rows = con.execute(q, args).fetchall()
    con.close()

    # Compare real datetimes, not strings. ts now carries '+00:00' and
    # commence_time carries 'Z', so a raw string compare still gets it wrong:
    # '...T23:20:00+00:00' sorts BEFORE '...T23:20:00Z' because '+' < 'Z', and a
    # snapshot taken just after first pitch would pass as a closing line.
    #
    # The naive-ts handling below stays for rows written before db.utc_now().
    # They are normalised by db.normalize_timestamps(), but a restored backup
    # or an old copy of the file can still hand this function one.
    best = None
    for row in rows:
        # feeds.parse_utc is the one place a stored timestamp becomes an aware
        # UTC datetime. It does exactly what the copy here did - Z to +00:00,
        # fromisoformat, default to UTC when naive - and returns None rather
        # than raising, so the unparseable-row skip is the same skip.
        start = parse_utc(row["commence_time"])
        taken = parse_utc(row["ts"])
        if start is None or taken is None:
            continue                      # unparseable timestamp; ignore the row
        if taken >= start:
            continue                      # taken at or after first pitch
        if best is None or taken > best[0]:
            best = (taken, start, row)

    if best is None:
        return None
    taken, start, row = best
    out = dict(row)
    mins = round((start - taken).total_seconds() / 60, 1)
    out["minutes_before_start"] = mins
    # The row is still returned when it is too early - it is useful to see how
    # close we got. is_closing is what decides whether CLV may be computed.
    out["is_closing"] = mins <= CLOSING_WINDOW_MIN
    return out


# Which book's price is treated as the fair line. Pinnacle runs the lowest
# margin and is the usual reference; the consensus of whatever books we have is
# the fallback, and which one was used is recorded per bet rather than assumed.
FAIR_BOOK = "pinnacle"


def fair_prob(con, odds_game_id: str, ts: str, side: str):
    """No-vig probability for `side` at that exact snapshot. (prob, source).

    De-vigged, because a price with the margin still in it is not a
    probability and cannot be used to compute expected value. Pinnacle if it
    priced that pull, otherwise the mean of the de-vigged probabilities across
    whatever books did.
    """
    rows = con.execute(
        "SELECT book, away_ml, home_ml FROM odds_snapshots"
        " WHERE game_id=? AND ts=? AND away_ml IS NOT NULL AND home_ml IS NOT NULL",
        (odds_game_id, ts)).fetchall()
    if not rows:
        return None, None
    pick = [r for r in rows if r["book"] == FAIR_BOOK]
    if pick:
        a, h = novig_probs(pick[0]["away_ml"], pick[0]["home_ml"])
        return (h if side == "home" else a), FAIR_BOOK
    probs = []
    for r in rows:
        a, h = novig_probs(r["away_ml"], r["home_ml"])
        probs.append(h if side == "home" else a)
    return sum(probs) / len(probs), "consensus"


def grade(bet_id: int, closing_line: int | None, won: bool | None,
          minutes_before_start: float | None = None):
    """Attach closing line + CLV, settle P&L. won=None for push.

    `minutes_before_start` is how early the price being used as the close was
    captured - closing_snapshot() reports it. CLV is only recorded when that
    is inside CLOSING_WINDOW_MIN. Outside it, or when it is not supplied at
    all, the bet is still settled for P&L but clv_pct stays NULL, so it does
    not count toward gate 2.

    Omitting the argument is treated as "unknown", which means ungraded. That
    is deliberate: the default for an unverifiable close should be to not
    count it, not to count it and hope.
    """
    con = connect()
    b = con.execute("SELECT * FROM bets WHERE bet_id=?", (bet_id,)).fetchone()
    valid_close = (closing_line is not None
                   and minutes_before_start is not None
                   and minutes_before_start <= CLOSING_WINDOW_MIN)
    clv = clv_pct(b["line_taken"], closing_line) if valid_close else None
    if won is None:
        result, pnl = "push", 0.0
    elif won:
        result = "W"
        pnl = round(b["stake"] * (american_to_decimal(b["line_taken"]) - 1), 2)
    else:
        result, pnl = "L", -b["stake"]
    con.execute(
        "UPDATE bets SET closing_line=?, clv_pct=?, result=?, pnl=? WHERE bet_id=?",
        (closing_line if valid_close else None, clv, result, pnl, bet_id))
    con.commit()
    con.close()
    if valid_close:
        note = f"CLV {clv:+.2f}% (close {minutes_before_start:.0f} min out)"
    elif minutes_before_start is None:
        note = "CLV not recorded - no closing time supplied"
    else:
        note = (f"CLV not recorded - nearest price was "
                f"{minutes_before_start:.0f} min before first pitch, "
                f"outside the {CLOSING_WINDOW_MIN} min window")
    print(f"bet {bet_id}: {result}  pnl {pnl:+.2f}  {note}")


def review(last_n: int = 50):
    """The model's recent bets, per mode. Placebo and manual bets are excluded:
    the placebo bets a random side on purpose, and manual bets have their own
    scoreboard. Mixing them in is how this once printed "positive CLV - edge
    is plausible" over one paper bet and one random-side bet."""
    con = connect()
    shown = False
    for mode in ("real", "paper"):
        rows = con.execute(
            "SELECT * FROM bets WHERE mode=? AND result IS NOT NULL"
            " ORDER BY bet_id DESC LIMIT ?", (mode, last_n)).fetchall()
        if not rows:
            continue
        shown = True
        # The kill criterion is over the last `last_n` bets that HAVE a CLV,
        # not the CLV-bearing subset of the last `last_n` settled ones - at
        # 20% coverage that subset never reaches 50, so it could never fire.
        clv_rows = con.execute(
            "SELECT clv_pct FROM bets WHERE mode=? AND clv_pct IS NOT NULL"
            " ORDER BY bet_id DESC LIMIT ?", (mode, last_n)).fetchall()
        n = len(rows)
        clvs = [r["clv_pct"] for r in clv_rows]
        pnl = sum(r["pnl"] for r in rows)
        staked = sum(r["stake"] for r in rows)
        wins = sum(1 for r in rows if r["result"] == "W")
        pushes = sum(1 for r in rows if r["result"] == "push")
        avg_clv = sum(clvs) / len(clvs) if clvs else 0.0
        roi = f"{pnl / staked:+.1%}" if staked else "n/a"
        print(f"[{mode}] last {n} settled | record {wins}-{n - wins - pushes}"
              + (f"-{pushes}" if pushes else "")
              + f" | ROI {roi} | avg CLV {avg_clv:+.2f}% over the last "
              f"{len(clvs)} with a usable close")
        if len(clvs) >= last_n and avg_clv < 0:
            print("*** KILL CRITERION HIT: rolling CLV negative over "
                  f"{last_n} bets. STOP BETTING. Diagnose before placing "
                  "another wager. ***")
    con.close()
    if not shown:
        print("No settled paper or real bets yet.")
        return
    # CLV against the same book's close includes line shopping, so a positive
    # number here is not evidence of edge. Gate 2 judges `info` instead.
    print(f"  CLV counts only closes within {CLOSING_WINDOW_MIN} min of first "
          f"pitch, and includes line shopping; gate 2 judges the model on "
          f"`info` - see `python model/validation.py`.")


if __name__ == "__main__":
    review()
