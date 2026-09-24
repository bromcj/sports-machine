"""Phase 2: grade any bet the same way the model's are graded. No credits.

    python run_daily.py bet --sport nfl --date 2026-09-28 \
        --game "Bills at Jets" --market h2h --side "Buffalo Bills" \
        --price -145 --book draftkings --stake 25 --tag research
    python run_daily.py bet --csv bets_inbox/        # drop-folder version
    python run_daily.py scoreboard                   # the report

WHAT THIS IS FOR. The three markets tested so far all came back null, but the
machinery built to test them grades a bet honestly: fair close, EV after vig,
the shop/info split, the closing window, coverage, and a pre-registered pass
rule. That is worth pointing at anything that actually gets bet.

The question it answers is **"is this handicapping worth continuing?"**, held
to the same standard the model was held to. Not "am I up this month" - a good
month is four bets of variance.

THE SHOP/INFO SPLIT IS WHY ENTRY RECORDS PRICES. A bet's edge decomposes:

    (1 + shop) x (1 + info) = 1 + ev

  shop  the price taken against the fair price AT THAT MOMENT. Pure line
        shopping. Real money, no forecasting skill, and books limit it.
  info  the fair price at the close against the fair price at the bet. This is
        the part that means you knew something.

A human's pick has no model snapshot to derive those from, so both the best
price available elsewhere and the fair price are recorded AT ENTRY. Without
that, a scoreboard can only say "you won" - not whether you won by beating the
number or by finding a better price for a number everyone had.

BOOSTED PRICES ARE GRADED AGAINST THE SAME FAIR CLOSE, so a boost's real value
shows up as `shop` rather than as skill.

IT NEVER STAKES MONEY. This records what was bet elsewhere; bets/engine.py
still refuses every real wager until the three gates pass.
"""
import argparse
import csv
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from db import connect, utc_now
from feeds import parse_utc
from bets.engine import american_to_prob, american_to_decimal, novig_probs
from bets.log import fair_prob, closing_snapshot, CLOSING_WINDOW_MIN

MODE = "manual"
TAGS = ("boost", "promo", "research", "sgp-leg", "")
MARKETS = ("h2h", "spreads", "totals")      # plus any prop market key


class EntryError(ValueError):
    """Refused at entry. The message says what to fix."""


# ------------------------------------------------------------------ entry ---

def resolve_game(con, sport: str, date: str, game: str):
    """Find the game. Refuse rather than guess, and refuse one already started.

    Matching is by substring against both team names on that date, which is
    forgiving enough for "Bills at Jets" and strict enough to refuse when two
    games match. `feeds` owns the cross-feed identity problem; this is only
    resolving a human's shorthand to a row that already exists.
    """
    rows = con.execute(
        "SELECT game_id, away, home, start_time_utc, status FROM games"
        " WHERE sport=? AND game_date=?", (sport, date)).fetchall()
    if not rows:
        raise EntryError(f"no {sport} games stored for {date}")
    needle = game.lower().replace(" at ", " ").replace(" vs ", " ").replace(
        " @ ", " ")
    words = [w for w in needle.split() if len(w) > 2]
    hits = []
    for r in rows:
        hay = f"{r['away']} {r['home']} {r['game_id']}".lower()
        if game.lower() == r["game_id"].lower() or all(w in hay for w in words):
            hits.append(r)
    if not hits:
        raise EntryError(
            f"{game!r} matched no {sport} game on {date}. Stored that day: "
            + "; ".join(f"{r['away']} at {r['home']}" for r in rows[:6]))
    if len(hits) > 1:
        raise EntryError(
            f"{game!r} matched {len(hits)} games: "
            + "; ".join(f"{r['away']} at {r['home']}" for r in hits)
            + ". Use the game_id.")
    r = hits[0]
    start = parse_utc(r["start_time_utc"])
    if start and start <= dt.datetime.now(dt.timezone.utc):
        raise EntryError(
            f"{r['away']} at {r['home']} started at {r['start_time_utc']}. "
            f"A bet entered after first pitch is not a forecast.")
    return r


def market_context(con, game_id: str, side: str):
    """(best_available, p_fair, fair_source) across whatever books we hold.

    Best available is the LONGEST price on that side - the most money back for
    the same outcome - which is what "could I have done better" means.
    """
    row = con.execute(
        "SELECT ts FROM odds_snapshots WHERE game_id=? ORDER BY ts DESC"
        " LIMIT 1", (game_id,)).fetchone()
    if not row:
        return None, None, None
    ts = row["ts"]
    prices = con.execute(
        "SELECT book, away_ml, home_ml FROM odds_snapshots WHERE game_id=?"
        " AND ts=? AND away_ml IS NOT NULL", (game_id, ts)).fetchall()
    g = con.execute("SELECT away, home FROM games WHERE game_id=?",
                    (game_id,)).fetchone()
    which = "home" if g and side.lower() in (g["home"] or "").lower() else "away"
    best = None
    for p in prices:
        ml = p["home_ml"] if which == "home" else p["away_ml"]
        if ml is None:
            continue
        # Longest price = smallest implied probability.
        if best is None or american_to_prob(ml) < american_to_prob(best):
            best = ml
    p_fair, src = fair_prob(con, game_id, ts, which)
    return best, p_fair, src


def enter(sport, date, game, market, side, price, book, stake,
          prop_line=None, player=None, tag="", manual_result=None) -> int:
    """Record one bet. Refuses anything it cannot resolve. Returns bet_id."""
    if tag not in TAGS:
        raise EntryError(f"tag must be one of {TAGS}, got {tag!r}")
    try:
        price = int(price)
    except (TypeError, ValueError):
        raise EntryError(f"price must be American odds, got {price!r}")
    if price in (0, -100, 100) or -100 < price < 100:
        raise EntryError(f"{price} is not a valid American price")
    if float(stake) <= 0:
        raise EntryError("stake must be positive")
    if market not in MARKETS and not player:
        raise EntryError(
            f"{market!r} looks like a prop market but no player was given")
    if market in ("spreads", "totals") and prop_line is None:
        raise EntryError(f"{market} needs a line (e.g. -3.5)")

    con = connect()
    g = resolve_game(con, sport, date, game)
    best, p_fair, src = market_context(con, g["game_id"], side)

    # model_prob, novig_market_prob, edge and kelly_fraction are NOT NULL, and
    # a manual bet has none of them: the human did not hand us a probability.
    #
    # Rather than rebuild the bets table to make them nullable - a destructive
    # ALTER on the one table that records money - they are filled with what
    # they honestly mean here. Both probabilities are the FAIR price at entry,
    # so `edge` is exactly 0.0: at entry a manual bet claims no edge, because
    # no claim was made. Whether there was one is measured afterwards, as shop
    # and info, which is the whole point of the scoreboard.
    #
    # kelly_fraction is 0.0 for the same reason: nothing here sized the bet.
    #
    # Fallback when we hold no price for the game: the break-even probability
    # of the price taken - what the bettor had to believe - which keeps `edge`
    # at 0 and never invents a market number we did not observe.
    p_entry = p_fair if p_fair is not None else american_to_prob(price)

    cur = con.execute(
        "INSERT INTO bets (mode, ts, game_id, sport, side, book, line_taken,"
        " stake, model_prob, novig_market_prob, edge, kelly_fraction,"
        " market, player, prop_line, tag, manual_result,"
        " best_available, p_fair_at_bet, fair_source, model_version)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (MODE, utc_now(), g["game_id"], sport, side, book, price, float(stake),
         p_entry, p_entry, 0.0, 0.0,
         market, player, prop_line, tag or None, manual_result, best, p_fair,
         src, "manual"))
    bet_id = cur.lastrowid
    con.commit()
    con.close()
    return bet_id


def from_csv(folder: str) -> list:
    """Every .csv in a drop folder. One row per bet, same fields as enter()."""
    out, errs = [], []
    d = Path(folder)
    if not d.exists():
        raise EntryError(f"{folder} does not exist")
    for f in sorted(d.glob("*.csv")):
        with open(f, newline="", encoding="utf-8") as fh:
            for i, row in enumerate(csv.DictReader(fh), 2):
                try:
                    out.append(enter(
                        row["sport"], row["date"], row["game"], row["market"],
                        row["side"], row["price"], row["book"], row["stake"],
                        row.get("prop_line") or None, row.get("player") or None,
                        row.get("tag", ""), row.get("manual_result") or None))
                except (EntryError, KeyError) as e:
                    errs.append(f"{f.name}:{i} {e}")
    for e in errs:
        print(f"  REFUSED {e}")
    return out


# ---------------------------------------------------------------- grading ---

def grade_all(sport: str | None = None) -> dict:
    """Settle and decompose every manual bet we can. Same path as paper bets.

    Deliberately reuses bets.log.closing_snapshot and fair_prob rather than
    doing its own: a scoreboard that graded a human's bets more generously
    than the model's would be worthless for comparing them.
    """
    con = connect()
    q = ("SELECT * FROM bets WHERE mode=? AND (result IS NULL"
         " OR ev_fair_close IS NULL)")
    args = [MODE]
    if sport:
        q += " AND sport=?"
        args.append(sport)
    tally = {"graded": 0, "no_close": 0, "no_result": 0, "outside_window": 0}
    for b in con.execute(q, args).fetchall():
        snap = closing_snapshot(b["game_id"])
        if snap is None:
            tally["no_close"] += 1
            continue
        g = con.execute("SELECT away, home, away_score, home_score, status"
                        " FROM games WHERE game_id=?",
                        (b["game_id"],)).fetchone()
        which = "home" if g and (b["side"] or "").lower() in (
            g["home"] or "").lower() else "away"
        p_close, src = fair_prob(con, snap["game_id"], snap["ts"], which)
        if not p_close or not b["p_fair_at_bet"]:
            tally["no_close"] += 1
            continue
        dec = american_to_decimal(b["line_taken"])
        shop = dec * b["p_fair_at_bet"] - 1
        info = p_close / b["p_fair_at_bet"] - 1
        ev = (1 + shop) * (1 + info) - 1
        inside = (snap.get("minutes_before_start") is not None
                  and snap["minutes_before_start"] <= CLOSING_WINDOW_MIN)
        if not inside:
            tally["outside_window"] += 1

        # Result: from scores where we hold them, else the typed-in one.
        won = None
        if b["market"] in (None, "h2h") and g and g["status"] == "final" \
                and g["away_score"] is not None:
            home_won = g["home_score"] > g["away_score"]
            won = home_won if which == "home" else not home_won
        elif b["manual_result"]:
            won = str(b["manual_result"]).strip().lower() in (
                "w", "win", "won", "1", "true", "yes")
        if won is None:
            tally["no_result"] += 1
        pnl = (None if won is None
               else round(b["stake"] * (dec - 1), 2) if won
               else -round(b["stake"], 2))
        con.execute(
            "UPDATE bets SET ev_fair_close=?, shop_pct=?, info_pct=?,"
            " novig_closing_prob=?, fair_source=?, result=?, pnl=?"
            " WHERE bet_id=?",
            (ev, shop, info if inside else None, p_close, src,
             (None if won is None else ("win" if won else "loss")), pnl,
             b["bet_id"]))
        tally["graded"] += 1
    con.commit()
    con.close()
    return tally


# -------------------------------------------------------------- scoreboard --

def _boot_ci(xs, n_boot=4000, seed=20260924):
    import random
    if not xs:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    means = []
    for _ in range(n_boot):
        s = [xs[rng.randrange(len(xs))] for _ in range(len(xs))]
        means.append(sum(s) / len(s))
    means.sort()
    return means[int(0.025 * n_boot)], means[int(0.975 * n_boot)]


def scoreboard(sport: str | None = None) -> dict:
    """n, ROI with a CI, EV, shop vs info, coverage - and the same gate verdict.

    Held to the standard the model is held to: 50+ graded, `info` clear of zero
    by 3 SE, coverage >= 75%. A human's picks do not get an easier bar than the
    model's, because the whole point is to compare them.
    """
    from model.validation import MIN_PAPER_BETS, PAPER_CLV_SIGMA, MIN_COVERAGE
    con = connect()
    q = "SELECT * FROM bets WHERE mode=?"
    args = [MODE]
    if sport:
        q += " AND sport=?"
        args.append(sport)
    rows = [dict(r) for r in con.execute(q, args).fetchall()]
    con.close()
    if not rows:
        return {"n": 0}

    settled = [r for r in rows if r["result"] in ("win", "loss")]
    graded = [r for r in settled if r["info_pct"] is not None]
    coverage = len(graded) / len(settled) if settled else 0.0

    def group(key):
        out = {}
        for r in rows:
            k = r.get(key) or ("h2h" if key == "market" else "(none)")
            out.setdefault(k, []).append(r)
        return out

    def stats(rs):
        s = [r for r in rs if r["result"] in ("win", "loss")]
        roi = [(r["pnl"] / r["stake"]) for r in s if r["pnl"] is not None
               and r["stake"]]
        gr = [r for r in rs if r["info_pct"] is not None]
        lo, hi = _boot_ci(roi)
        return {
            "n": len(rs), "settled": len(s), "graded": len(gr),
            "roi": (sum(roi) / len(roi)) if roi else None,
            "roi_lo": lo, "roi_hi": hi,
            "ev": _mean([r["ev_fair_close"] for r in rs]),
            "shop": _mean([r["shop_pct"] for r in rs]),
            "info": _mean([r["info_pct"] for r in gr]),
        }

    infos = [r["info_pct"] for r in graded if r["info_pct"] is not None]
    mean_info = _mean(infos)
    sd = _stdev(infos)
    se = sd / (len(infos) ** 0.5) if infos else 0.0
    verdict = {
        "enough": len(graded) >= MIN_PAPER_BETS,
        "convincing": bool(infos) and se > 0
                      and (mean_info - PAPER_CLV_SIGMA * se) > 0,
        "covered": coverage >= MIN_COVERAGE,
    }
    return {"n": len(rows), "overall": stats(rows), "coverage": coverage,
            "by_tag": {k: stats(v) for k, v in group("tag").items()},
            "by_market": {k: stats(v) for k, v in group("market").items()},
            "by_sport": {k: stats(v) for k, v in group("sport").items()},
            "verdict": verdict,
            "passes": all(verdict.values())}


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _stdev(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def _fmt(v, pct=True):
    if v is None:
        return "     -"
    return f"{100 * v:+6.2f}%" if pct else f"{v:6.2f}"


def report(sport: str | None = None) -> int:
    s = scoreboard(sport)
    if not s.get("n"):
        print("No manual bets recorded. Add one with:\n"
              "  python run_daily.py bet --sport nfl --date YYYY-MM-DD "
              "--game \"Bills at Jets\" --market h2h --side \"Buffalo Bills\" "
              "--price -145 --book draftkings --stake 25 --tag research")
        return 0
    o = s["overall"]
    print("=" * 74)
    print("SCOREBOARD - your bets, graded the way the model's are")
    print("=" * 74)
    print(f"\n{s['n']} bet(s), {o['settled']} settled, {o['graded']} graded "
          f"for CLV  (coverage {100 * s['coverage']:.0f}%)")
    print(f"\n  ROI      {_fmt(o['roi'])}  "
          f"[{_fmt(o['roi_lo'])}, {_fmt(o['roi_hi'])}]  bootstrap 95%")
    print(f"  EV       {_fmt(o['ev'])}   against the fair close")
    print(f"  shop     {_fmt(o['shop'])}   the price you got")
    print(f"  info     {_fmt(o['info'])}   what you knew")
    print("\n  shop is real money and books limit it. info is the half that "
          "means\n  you knew something. They multiply to EV.")

    for label, key in (("tag", "by_tag"), ("market", "by_market"),
                       ("sport", "by_sport")):
        print(f"\nby {label}")
        print(f"  {label:14s} {'n':>4s} {'settled':>8s} {'ROI':>8s} "
              f"{'EV':>8s} {'shop':>8s} {'info':>8s}")
        for k, v in sorted(s[key].items()):
            print(f"  {str(k):14s} {v['n']:>4} {v['settled']:>8} "
                  f"{_fmt(v['roi'])} {_fmt(v['ev'])} {_fmt(v['shop'])} "
                  f"{_fmt(v['info'])}")

    v = s["verdict"]
    print("\n" + "-" * 74)
    print("Is this handicapping worth continuing? Same bar as the model.")
    print("-" * 74)
    print(f"  50+ graded bets            {'YES' if v['enough'] else 'not yet'}")
    print(f"  info clear of zero by 3 SE {'YES' if v['convincing'] else 'no'}")
    cov = "YES" if v["covered"] else f"no ({100 * s['coverage']:.0f}%)"
    print(f"  coverage >= 75%            {cov}")
    print(f"\n  VERDICT: {'worth continuing' if s['passes'] else 'NOT PROVEN'}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport")
    raise SystemExit(report(ap.parse_args().sport))
