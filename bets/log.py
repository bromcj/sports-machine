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
               novig_market_prob, edge, kelly_fraction, model_version):
    con = connect()
    con.execute(
        """INSERT INTO bets (ts, game_id, sport, side, book, line_taken, stake, model_prob,
                             novig_market_prob, edge, kelly_fraction, model_version)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (utc_now(), game_id, sport, side, book, line_taken, stake,
         model_prob, novig_market_prob, edge, kelly_fraction, model_version))
    con.commit()
    con.close()


def odds_twin(con, game_id: str) -> tuple[str | None, str | None]:
    """Find the odds-feed row for the same real game. Returns (game_id, note).

    The three feeds never share an identifier. The MLB Stats API calls a game
    'mlb-823494', The Odds API calls the same game 'mlb-394e1e2b849c...', and
    ESPN calls it 'mlb-espn-401817028'. Nothing maps between them, so a row
    with a final score has no odds and a row with odds has no final score -
    measured on the live database: 12,051 scored games, 36 games with odds,
    and ZERO with both.

    Bets carry one game_id, so without this resolution either the closing line
    or the result is unreachable and CLV can never be computed. Matching is on
    (game_date, away, home), which holds because all three feeds spell the 30
    clubs identically. A doubleheader makes that ambiguous, and those return
    None rather than a guess - a CLV number attached to the wrong game of a
    doubleheader is worse than no number.
    """
    g = con.execute("SELECT game_date, away, home FROM games WHERE game_id=?",
                    (game_id,)).fetchone()
    if g is None:
        return None, "no such game_id"
    rows = con.execute(
        "SELECT DISTINCT o.game_id FROM odds_snapshots o JOIN games gg"
        " ON gg.game_id = o.game_id"
        " WHERE gg.game_date=? AND gg.away=? AND gg.home=?",
        (g["game_date"], g["away"], g["home"])).fetchall()
    if not rows:
        return None, "no odds feed row for this matchup"
    if len(rows) > 1:
        return None, f"ambiguous: {len(rows)} odds rows (doubleheader?)"
    return rows[0]["game_id"], None


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
    out["minutes_before_start"] = round((start - taken).total_seconds() / 60, 1)
    return out


def grade(bet_id: int, closing_line: int, won: bool | None):
    """Attach closing line + CLV, settle P&L. won=None for push."""
    con = connect()
    b = con.execute("SELECT * FROM bets WHERE bet_id=?", (bet_id,)).fetchone()
    clv = clv_pct(b["line_taken"], closing_line)
    if won is None:
        result, pnl = "push", 0.0
    elif won:
        result = "W"
        pnl = round(b["stake"] * (american_to_decimal(b["line_taken"]) - 1), 2)
    else:
        result, pnl = "L", -b["stake"]
    con.execute(
        "UPDATE bets SET closing_line=?, clv_pct=?, result=?, pnl=? WHERE bet_id=?",
        (closing_line, clv, result, pnl, bet_id))
    con.commit()
    con.close()
    print(f"bet {bet_id}: {result}  pnl {pnl:+.2f}  CLV {clv:+.2f}%")


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
          f"ROI {pnl / staked:+.1%} | avg CLV {avg_clv:+.2f}%")
    if len(clvs) >= 50 and avg_clv < 0:
        print("*** KILL CRITERION HIT: rolling CLV negative over 50+ bets. "
              "STOP BETTING. Diagnose before placing another wager. ***")
    elif avg_clv > 0:
        print("Process check: positive CLV — edge is plausible, stay the course.")


if __name__ == "__main__":
    review()
