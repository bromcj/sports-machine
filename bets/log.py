"""Bet log writes, closing-line grading, and the weekly review.

Kill criterion enforced in review(): rolling 50-bet average CLV < 0
=> STOP BETTING, diagnose. Judge process by CLV, not last week's P&L.
"""
import datetime as dt
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
from db import connect, utc_now
from bets.engine import clv_pct, american_to_decimal


def record_bet(game_id, sport, side, book, line_taken, stake, model_prob,
               novig_market_prob, edge, kelly_fraction, model_version,
               mode: str = "real"):
    """Log a wager. Returns its bet_id.

    `mode` defaults to 'real' deliberately. Gate 2 counts PAPER bets, so an
    unlabelled bet contributes nothing toward opening the tap - the mistake
    costs you a slower gate, not an unearned one. The reverse default would
    let anything that forgot the flag help justify staking money.
    """
    if mode not in ("paper", "real"):
        raise ValueError(f"mode must be 'paper' or 'real', got {mode!r}")
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
from feeds import odds_twin, pregame_books           # noqa: E402,F401


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
        try:
            start = dt.datetime.fromisoformat(row["commence_time"].replace("Z", "+00:00"))
            taken = dt.datetime.fromisoformat(row["ts"])
        except (ValueError, AttributeError):
            continue                      # unparseable timestamp; ignore the row
        if taken.tzinfo is None:
            taken = taken.replace(tzinfo=dt.timezone.utc)
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
    con = connect()
    rows = con.execute(
        "SELECT * FROM bets WHERE result IS NOT NULL ORDER BY bet_id DESC LIMIT ?",
        (last_n,)).fetchall()
    con.close()
    if not rows:
        print("No graded bets yet.")
        return
    n = len(rows)
    clvs = [r["clv_pct"] for r in rows if r["clv_pct"] is not None]
    pnl = sum(r["pnl"] for r in rows)
    staked = sum(r["stake"] for r in rows)
    wins = sum(1 for r in rows if r["result"] == "W")
    avg_clv = sum(clvs) / len(clvs) if clvs else 0.0
    print(f"Last {n} bets | record {wins}-{n - wins} | "
          f"ROI {pnl / staked:+.1%} | avg CLV {avg_clv:+.2f}% over {len(clvs)} bets")
    # Settled bets whose close was too early to trust are invisible in the CLV
    # line above. Saying so keeps "50 bets" from meaning two different things.
    ungraded = n - len(clvs)
    if ungraded:
        print(f"  {ungraded} of these have no usable closing line "
              f"(none captured within {CLOSING_WINDOW_MIN} min of first pitch) "
              f"and do not count toward gate 2.")
    if len(clvs) >= 50 and avg_clv < 0:
        print("*** KILL CRITERION HIT: rolling CLV negative over 50+ bets. "
              "STOP BETTING. Diagnose before placing another wager. ***")
    elif avg_clv > 0:
        print("Process check: positive CLV — edge is plausible, stay the course.")


if __name__ == "__main__":
    review()
