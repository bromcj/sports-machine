"""Score today's games with the saved model, then compare those numbers to the
market to see whether anything is worth a bet.

Two joins have to happen and only one of them is trivial.

  features -> model      keyed on game_id, and the model carries its own column
                         order, so this is a lookup.

  prediction -> odds     NOT keyed on anything shared. The MLB Stats API calls
                         tonight's game 'mlb-823494'; the Odds API calls the
                         same game 'mlb-394e1e2b849c...'. Nothing links them.
                         They are matched on (date, away team, home team),
                         which works because both sources spell all 30 clubs
                         identically - verified, 15 of 15 on a full slate.

That match is ambiguous for a doubleheader: two games, same date, same two
clubs. Rather than guess which price belongs to which game, those are reported
as ambiguous and skipped. A pick attached to the wrong game of a doubleheader
is worse than no pick, and the CLV it produces would be fiction.
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


def _odds_for(con, date: str, away: str, home: str):
    """Latest PREGAME price per book for the odds-API twin of this game.

    The odds API keeps serving a market after first pitch, switching to in-play
    prices. Taking the newest snapshot per book therefore grabs a live line once
    a game is under way: a real pull at 12:24am returned Giants -10000, a 97.1%
    "market probability", because they were already winning. Only snapshots
    taken strictly before commence_time are pregame prices.

    Timestamps are compared as datetimes, not strings - ts is naive UTC and
    commence_time carries a 'Z', so a string compare mis-orders them.

    Returns (list_of_book_rows, note). note is set when the match is unsafe.
    """
    twins = con.execute(
        "SELECT DISTINCT game_id FROM games WHERE sport='mlb' AND game_date=?"
        " AND away=? AND home=? AND SUBSTR(game_id,5) GLOB '*[^0-9]*'"
        " AND game_id NOT LIKE 'mlb-espn-%'", (date, away, home)).fetchall()
    if not twins:
        return [], "no odds for this game"
    if len(twins) > 1:
        return [], f"ambiguous: {len(twins)} odds records (doubleheader?)"

    rows = con.execute(
        "SELECT * FROM odds_snapshots WHERE game_id=? AND away_ml IS NOT NULL",
        (twins[0]["game_id"],)).fetchall()
    latest = {}
    for r in rows:
        if not r["commence_time"]:
            continue
        try:
            start = dt.datetime.fromisoformat(r["commence_time"].replace("Z", "+00:00"))
            taken = dt.datetime.fromisoformat(r["ts"])
        except (ValueError, AttributeError):
            continue
        if taken.tzinfo is None:
            taken = taken.replace(tzinfo=dt.timezone.utc)
        if taken >= start:
            continue                      # in-play price, not a market opinion
        prev = latest.get(r["book"])
        if prev is None or taken > prev[0]:
            latest[r["book"]] = (taken, r)
    books = [r for _, r in latest.values()]
    if not books:
        return [], "only in-play prices (game already started)"
    return books, None


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
