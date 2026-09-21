"""Bet log writes, closing-line grading, and the weekly review.

Kill criterion enforced in review(): rolling 50-bet average CLV < 0
=> STOP BETTING, diagnose. Judge process by CLV, not last week's P&L.
"""
import datetime as dt
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
from db import connect
from bets.engine import clv_pct, american_to_decimal


def record_bet(game_id, sport, side, book, line_taken, stake, model_prob,
               novig_market_prob, edge, kelly_fraction, model_version):
    con = connect()
    con.execute(
        """INSERT INTO bets (ts, game_id, sport, side, book, line_taken, stake, model_prob,
                             novig_market_prob, edge, kelly_fraction, model_version)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (dt.datetime.utcnow().isoformat(), game_id, sport, side, book, line_taken, stake,
         model_prob, novig_market_prob, edge, kelly_fraction, model_version))
    con.commit()
    con.close()


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
