"""Each strategy's own gates, in validation.json, under its own name.

  gate 1   record_backtest(): its pre-registered backtest. Refused unless the
           strategy is registered and its experiment is a heading in
           docs/experiments.md. This is the deliberate act that CREATES the
           strategy's block, by hand, in a committed change.
  gate 2   score(): model.validation.record_paper - unchanged, the sport
           models' rules - on the strategy's own graded paper positions,
           its own coverage, its own placebo. It only ever updates the
           paper_trading part of a block that already exists, so the
           scheduled job's guard (only_paper_changed) can always discard it.
  gate 3   model.validation.arm(name): a person. Nothing here calls it.

LOOKS. score() re-tests gate 2 at most MAX_LOOKS_PER_DAY times per ET day and
logs every look in gate_looks, so the cadence is the one the simulation
covers. Measured (docs/gates.md): at 3 SE a no-skill strategy grading 50 a
day passes by luck 2.3% of the time over 120 days at two looks a day, and
2.9% if it were re-tested after every graded position. More looks, looser
gate; the cap keeps it where it was measured.
"""
import datetime as dt
from pathlib import Path

from feeds import ET, parse_utc
from model import validation
from scanner import strategies
from scanner.store import canon_ts

EXPERIMENTS = Path(__file__).parent.parent / "docs" / "experiments.md"
MAX_LOOKS_PER_DAY = 2
# A position whose market resolved this long ago should have settled.
SETTLE_GRACE_H = 36


def record_backtest(name: str, experiment: str, passed: bool, reason: str,
                    evidence: dict) -> dict:
    strategies.get(name)                             # must be registered
    # The WHOLE heading, not a prefix: "E1" alone would match the unrelated
    # 2026-09-23 "E1. The market's recipe vs reality's recipe".
    headings = {ln.lstrip("#").strip() for ln in
                EXPERIMENTS.read_text(encoding="utf-8").splitlines()
                if ln.startswith("#")}
    if experiment.strip() not in headings:
        raise ValueError(f"{experiment!r} is not a heading in docs/experiments.md"
                         f" (the whole heading): pre-register it before recording"
                         f" a result")
    return validation.record_backtest(name, experiment, passed, reason, evidence)


def looks_today(con, name: str, now) -> int:
    d = parse_utc(canon_ts(now)).astimezone(ET).date()
    start = dt.datetime(d.year, d.month, d.day, tzinfo=ET)
    return con.execute("SELECT COUNT(*) FROM gate_looks WHERE name=? AND looked_at >= ?"
                       " AND looked_at < ?",
                       (name, canon_ts(start),
                        canon_ts(start + dt.timedelta(days=1)))).fetchone()[0]


def _slot(pos) -> str:
    when = parse_utc(pos["event_start"]) or parse_utc(pos["opened_at"])
    return validation.slot_of(when.astimezone(ET).hour)


def measured(con, name: str, metric: str, now) -> dict:
    """The strategy's graded values, placebo values, slots and coverage."""
    col = "info" if metric == "info" else "realized_ev"
    rows = {}
    for mode in ("paper", "placebo"):
        # Graded AND settled. The scanner grades at the start and settles
        # later, so a graded position that has not settled would otherwise
        # stand in for a settled one that could not be graded (review
        # 2026-09-25: gate 2 passed at a true coverage of 17%).
        rows[mode] = con.execute(
            f"SELECT p.*, m.event_start FROM paper_positions p"
            f" LEFT JOIN markets m ON m.market_id = p.market_id"
            f" WHERE p.strategy=? AND p.mode=? AND p.{col} IS NOT NULL"
            f" AND p.result IS NOT NULL"
            f" ORDER BY p.position_id", (name, mode)).fetchall()
    settled = con.execute("SELECT COUNT(*) FROM paper_positions WHERE strategy=?"
                          " AND mode='paper' AND result IS NOT NULL", (name,)).fetchone()[0]
    if metric == "info":
        coverage = len(rows["paper"]) / settled if settled else None
    else:
        # Every settled position has a realized EV, so the honest question is
        # how many of the positions that SHOULD have settled did - counted
        # over the same positions top and bottom, or a recent settlement
        # stands in for an old one that never settled. A position with no
        # resolves_at cannot be shown not to be due yet, so it counts as due:
        # against coverage until it settles (fails closed).
        due_before = canon_ts(parse_utc(canon_ts(now)) - dt.timedelta(hours=SETTLE_GRACE_H))
        due, settled_due = con.execute(
            "SELECT COUNT(*), COUNT(result) FROM paper_positions WHERE strategy=?"
            " AND mode='paper' AND (resolves_at IS NULL OR resolves_at < ?)",
            (name, due_before)).fetchone()
        coverage = settled_due / due if due else None
    return {"values": [r[col] for r in rows["paper"]],
            "slots": [_slot(r) for r in rows["paper"]],
            "placebo": [r[col] for r in rows["placebo"]],
            "coverage": coverage, "settled": settled}


def score(con, name: str, now) -> dict | None:
    """Re-test one strategy's gate 2. None, with nothing written, when it
    has no gate record yet or has already been looked at twice today."""
    s = strategies.get(name)
    if validation.status(name) is None:
        print(f"  {name}: no gate record yet - record its backtest first")
        return None
    if looks_today(con, name, now) >= MAX_LOOKS_PER_DAY:
        print(f"  {name}: gate 2 already re-tested {MAX_LOOKS_PER_DAY} times today")
        return None
    m = measured(con, name, s.metric, now)
    r = validation.record_paper(name, m["values"], slots=m["slots"],
                                coverage=m["coverage"], placebo=m["placebo"])
    con.execute("INSERT INTO gate_looks (name, looked_at, metric, n, mean, se, t,"
                " coverage, passed, reason) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (name, canon_ts(now), s.metric, r["n_bets"], r["avg_clv"], r["se_clv"],
                 r["t_stat"], r["coverage"], 1 if r["passed"] else 0, r["reason"]))
    return r


def main(argv) -> int:
    """python run_daily.py strategies [--score]

    Every registered strategy, with its gate verdict. --score re-tests each
    one's gate 2 (at most twice an ET day) - free, and like `paper` it
    rewrites only the gate-2 part of validation.json.
    """
    import db
    reg = strategies.load()
    if not reg:
        print("No strategies registered. They arrive in Phases B and E of"
              " docs/briefs/2026-09-25-next-task.md, each with its own entry in"
              " docs/experiments.md first.")
        return 0
    con = db.connect()
    try:
        now = dt.datetime.now(dt.timezone.utc)
        for name, s in sorted(reg.items()):
            print(f"{name}  ({s.metric}; venues {', '.join(s.venues)};"
                  f" gate 1: {s.experiment})")
            if "--score" in argv:
                r = score(con, name, now)
                con.commit()
                if r:
                    print(f"  gate 2 re-tested: {r['reason']}")
            print(f"  {validation.explain(name) if validation.status(name) else 'no gate record yet'}")
            print(f"  looked at {looks_today(con, name, now)} time(s) today")
    finally:
        con.close()
    return 0
