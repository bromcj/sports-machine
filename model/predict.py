"""Score today's games with the saved model, then compare those numbers to the
market to see whether anything is worth a bet.

Two joins have to happen and only one of them is trivial.

  features -> model      keyed on game_id, and the model carries its own column
                         order, so this is a lookup.

  prediction -> odds     NOT keyed on anything shared. The MLB Stats API calls
                         tonight's game 'mlb-823494'; the Odds API calls the
                         same game 'mlb-394e1e2b849c...'. Nothing links them.
                         feeds.py matches them on teams plus first-pitch time.

That match used to be on (date, away, home), and the note here claimed it was
"verified, 15 of 15 on a full slate". It was verified on a day that happened
not to expose the bug: the odds feed dates a game by its UTC date, so anything
starting after 8pm ET carries a date one day ahead, and in a series Wednesday's
game took TUESDAY's prices. Measured before the fix, 5 of 20 matched games were
wrong - all late west-coast starts, which are exactly the games whose closes
qualify for CLV grading.

Matching on time also resolves doubleheaders, which date matching could only
refuse.
"""
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from bets.engine import evaluate, novig_probs
from db import connect
from feeds import SQL_STATS_API, pregame_books
from model.persist import load as load_model, predict_margin, win_prob


def predict_for_date(date: str | None = None, sport: str = "mlb",
                     verbose: bool = True) -> int:
    """Score every game with a feature row, and store the probability."""
    date = date or dt.date.today().isoformat()
    bundle = load_model(sport)

    con = connect()
    rows = con.execute(
        "SELECT f.game_id, f.payload, g.away, g.home FROM features f"
        " JOIN games g ON g.game_id = f.game_id"
        " WHERE f.sport = ? AND g.game_date = ?", (sport, date)).fetchall()
    if not rows:
        con.close()
        if verbose:
            print(f"No feature rows for {date}. Run features/build.py first.")
        return 0

    feats = pd.DataFrame([json.loads(r["payload"]) for r in rows])
    margins = predict_margin(bundle, feats)
    probs = win_prob(bundle, margins)

    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    for r, m, p in zip(rows, margins, probs):
        con.execute(
            "INSERT OR REPLACE INTO predictions (game_id, sport, ts,"
            " model_version, proj_margin, home_win_prob) VALUES (?,?,?,?,?,?)",
            (r["game_id"], sport, ts,
             f"ridge-{sport}-{bundle['trained_through']}", float(m), float(p)))
    con.commit()

    if verbose:
        stale = json.loads(rows[0]["payload"]).get("_stale_days")
        print(f"Model: {bundle['n_train_rows']} rows through "
              f"{bundle['trained_through']}, k={bundle['k']}")
        if stale:
            print(f"Features built from Statcast {stale} day(s) stale.")
        print(f"\n{'matchup':42s} {'proj margin':>11s} {'home win':>9s}")
        for r, m, p in sorted(zip(rows, margins, probs), key=lambda x: -x[2]):
            print(f"  {r['away'][:18]:18s} @ {r['home'][:18]:18s} "
                  f"{m:+8.2f}   {p:7.1%}")
    con.close()
    return len(rows)


def _odds_for(con, date, away, home, game_id=None):
    """Kept for callers that pass (date, away, home). Prefer feeds.pregame_books.

    Date-and-teams is exactly the matching that handed late games the previous
    night's prices, so this now resolves the game_id first and matches on time.
    """
    if game_id is None:
        row = con.execute(
            "SELECT game_id FROM games WHERE sport='mlb' AND game_date=?"
            f" AND away=? AND home=? AND ({SQL_STATS_API})",
            (date, away, home)).fetchone()
        if row is None:
            return [], "no scheduled game for this matchup"
        game_id = row["game_id"]
    return pregame_books(con, game_id)


def picks(date: str | None = None, bankroll: float = 1000.0,
          sport: str = "mlb") -> list[dict]:
    """Join predictions to the market and run each through the bet engine."""
    date = date or dt.date.today().isoformat()
    con = connect()
    preds = con.execute(
        "SELECT p.game_id, p.home_win_prob, p.proj_margin, g.away, g.home"
        " FROM predictions p JOIN games g ON g.game_id = p.game_id"
        " WHERE p.sport=? AND g.game_date=?", (sport, date)).fetchall()

    out = []
    print(f"\n{'matchup':40s} {'model':>7s} {'market':>7s} {'edge':>7s}  verdict")
    print("-" * 88)
    for p in preds:
        books, note = _odds_for(con, date, p["away"], p["home"])
        label = f"{p['away'][:17]} @ {p['home'][:17]}"
        if note:
            print(f"  {label:38s} {p['home_win_prob']:6.1%} {'-':>7s} {'-':>7s}  {note}")
            continue
        # Compute the edge here rather than reading it off evaluate(): when the
        # guard refuses a sport it returns early with no edge fields, and the
        # number is exactly what you want to see while a sport is still barred.
        best = None
        for b in books:
            nv_away, nv_home = novig_probs(b["away_ml"], b["home_ml"])
            edge = max(p["home_win_prob"] - nv_home,
                       (1 - p["home_win_prob"]) - nv_away)
            r = evaluate(sport, p["home_win_prob"], b["away_ml"], b["home_ml"],
                         bankroll)
            if best is None or edge > best[0]:
                best = (edge, b, r)
        edge, b, r = best
        _, mkt_home = novig_probs(b["away_ml"], b["home_ml"])
        verdict = (f"BET {r['side']} {r['line']:+d} @{b['book']} "
                   f"stake ${r['stake']}" if r["bet"] else r["reason"])
        print(f"  {label:38s} {p['home_win_prob']:6.1%} {mkt_home:6.1%} "
              f"{edge:+6.1%}  {verdict[:38]}")
        out.append({"game_id": p["game_id"], "away": p["away"], "home": p["home"],
                    "model_prob": p["home_win_prob"], "market_prob": mkt_home,
                    "edge": edge, **r})
    con.close()
    return out


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else None
    predict_for_date(d)
    picks(d)
