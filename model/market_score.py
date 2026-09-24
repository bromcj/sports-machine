"""Score every finished game against the market, bet or no bet.

    python run_daily.py market

Gate 1 is judged on bought historical closing lines (market_close), which
only change when resolve_market_close.py is run by hand. This is the running
head-to-head against the market for games as they finish, scored with the
prediction the live system actually made before first pitch.

It uses every finished game with a usable close, not only games that were bet.
That matters: paper bets exist only where the model found an edge, which is a
selected subset and a flattering one. Scoring the whole slate is the unbiased
comparison.

IT CLEARS NOTHING. record() is not called from here and gate 1 does not read
this table. It is a measurement, reported with an honest interval, and wiring
it into a gate is a separate decision that needs its own design review.

The interval is a DAY-BLOCK bootstrap: games on the same day share a slate, a
weather pattern and one model fit, so resampling individual games would treat
correlated observations as independent and report an interval that is too
narrow. Resampling whole days does not.
"""
import datetime as dt
import math
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from bets.log import CLOSING_WINDOW_MIN, closing_snapshot, fair_prob
from db import connect, utc_now
from feeds import SQL_STATS_API, parse_utc

EPS = 1e-6

# Below this many distinct days the day-block bootstrap has too few blocks
# to resample, and its interval is decoration rather than information.
MIN_DAYS_FOR_CI = 10


def _ll(prob: float, won: int) -> float:
    p = min(max(prob, EPS), 1 - EPS)
    return -(math.log(p) if won else math.log(1 - p))


def score_finished(sport: str = "mlb", limit_days: int = 30) -> dict:
    """Score finished games that have a usable close. Idempotent per game."""
    con = connect()
    since = (dt.date.today() - dt.timedelta(days=limit_days)).isoformat()
    games = con.execute(
        f"SELECT game_id, game_date, away_score, home_score, start_time_utc"
        f" FROM games WHERE sport=? AND status='final' AND ({SQL_STATS_API})"
        f"   AND away_score IS NOT NULL AND game_date >= ?"
        f"   AND game_id NOT IN (SELECT game_id FROM market_scores)",
        (sport, since)).fetchall()

    tally = {"scored": 0, "no_close": 0, "no_prediction": 0}
    for g in games:
        start = parse_utc(g["start_time_utc"])
        if start is None:
            tally["no_close"] += 1
            continue
        # The latest prediction made BEFORE first pitch. Append-only
        # predictions are what make this answerable at all - the previous
        # schema overwrote them, so "what did it think beforehand" was gone.
        pred = con.execute(
            "SELECT prediction_id, home_win_prob FROM predictions"
            " WHERE game_id=? AND created_at < ? ORDER BY created_at DESC LIMIT 1",
            (g["game_id"], start.isoformat())).fetchone()
        if pred is None:
            tally["no_prediction"] += 1
            continue
        snap = closing_snapshot(g["game_id"])
        if snap is None or not snap["is_closing"]:
            tally["no_close"] += 1
            continue
        p_mkt, src = fair_prob(con, snap["game_id"], snap["ts"], "home")
        if not p_mkt:
            tally["no_close"] += 1
            continue
        won = 1 if g["home_score"] > g["away_score"] else 0
        con.execute(
            "INSERT OR IGNORE INTO market_scores (game_id, sport, game_date,"
            " scored_at, prediction_id, model_prob, market_prob, fair_source,"
            " home_won, model_ll, market_ll) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (g["game_id"], sport, g["game_date"], utc_now(),
             pred["prediction_id"], pred["home_win_prob"], p_mkt, src, won,
             _ll(pred["home_win_prob"], won), _ll(p_mkt, won)))
        tally["scored"] += 1
    con.commit()
    con.close()
    return tally


def report(sport: str = "mlb", trials: int = 4000) -> dict | None:
    """Running model-vs-market totals with a day-block bootstrap interval."""
    import random
    con = connect()
    rows = con.execute(
        "SELECT game_date, model_ll, market_ll FROM market_scores WHERE sport=?",
        (sport,)).fetchall()
    con.close()
    if not rows:
        return None
    by_day = {}
    for r in rows:
        by_day.setdefault(r["game_date"], []).append(r["market_ll"] - r["model_ll"])
    days = list(by_day.values())
    flat = [d for g in days for d in g]
    mean = sum(flat) / len(flat)

    # Resample whole DAYS. Same-day games share a slate and one model fit, so
    # treating them as independent would report an interval that is too narrow.
    rng = random.Random(11)
    means = []
    for _ in range(trials):
        pick = [days[rng.randrange(len(days))] for _ in range(len(days))]
        vals = [d for g in pick for d in g]
        means.append(sum(vals) / len(vals))
    means.sort()
    lo, hi = means[int(0.05 * trials)], means[int(0.95 * trials) - 1]
    return {"n_games": len(flat), "n_days": len(days), "mean_edge": mean,
            "ci90": (lo, hi), "beats_market": mean > 0,
            # Resampling 3 days can only ever produce 3 distinct days, so the
            # interval is not an interval yet. Say so rather than printing a
            # confident-looking range.
            "ci_meaningful": len(days) >= MIN_DAYS_FOR_CI}


def run(sport: str = "mlb"):
    t = score_finished(sport)
    print(f"Market scoring [{sport}]: +{t['scored']} newly scored "
          f"({t['no_close']} without a usable close, "
          f"{t['no_prediction']} without a pre-game prediction)")
    r = report(sport)
    if r is None:
        print("  nothing scored yet - needs a finished game with a close "
              f"inside {CLOSING_WINDOW_MIN} min of first pitch")
        return
    lo, hi = r["ci90"]
    print(f"  {r['n_games']} games over {r['n_days']} days")
    if not r["ci_meaningful"]:
        print(f"  mean log-loss edge {r['mean_edge']:+.5f}  "
              f"(no interval yet: {r['n_days']} day(s), need {MIN_DAYS_FOR_CI})")
        print("  -> too early to say anything")
    else:
        verdict = ("AHEAD of the market" if lo > 0 else
                   "BEHIND the market" if hi < 0 else
                   "indistinguishable from the market")
        print(f"  mean log-loss edge {r['mean_edge']:+.5f}  "
              f"(90% CI {lo:+.5f} .. {hi:+.5f}, day-block bootstrap)")
        print(f"  -> {verdict}")
    print("  This clears no gate. It is a measurement, not a verdict.")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "mlb")
