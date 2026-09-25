"""Each strategy's own gates, in validation.json, under its own name.

  gate 1   record_backtest(): its pre-registered backtest. Refused unless the
           strategy is registered, the experiment is the one it is
           registered against, that is a heading in docs/experiments.md
           above the Results log, and no other name's gate record holds it
           (a retired strategy's included) - and, once a block exists, the
           experiment and metric it holds: a new definition is a new name.
           This is the deliberate act that CREATES the strategy's block, by
           hand, in a committed change, and the block pins the definition.
  gate 2   score(): model.validation.record_paper - unchanged, the sport
           models' rules - on the strategy's own graded paper positions,
           one value per EVENT (measured()), its own coverage, its own
           placebo. It only ever updates the paper_trading part of a block
           that already exists, so the scheduled job's guard
           (only_paper_changed) may discard it - or refuses to, when
           discarding would bring back a pass.
           Refusals sports do not need: a realized_ev strategy cannot pass
           yet (UNCALIBRATED, below), nor can one whose placebo has fewer
           than 50 graded events, nor one whose file now holds a different
           experiment or metric from its gate record (redefined()).
  gate 3   model.validation.arm(name): a person. Nothing here calls it. It
           refuses a redefined strategy too.

LOOKS. score() re-tests gate 2 at most MAX_LOOKS_PER_DAY times per ET day and
logs every look in gate_looks, so the cadence is the one the simulation
covers. Measured (docs/gates.md): at 3 SE a no-skill strategy grading 50 a
day passes by luck 2.3% of the time over 120 days at two looks a day, and
about 3% if it were re-tested after every graded position. That simulation drew
near-normal values - the info metric - and says nothing about realized_ev.
More looks, looser gate; the cap keeps it where it was measured.
"""
import datetime as dt
import re
from pathlib import Path

from feeds import ET, parse_utc
from model import validation
from scanner import strategies
from scanner.store import canon_ts

EXPERIMENTS = Path(__file__).parent.parent / "docs" / "experiments.md"
MAX_LOOKS_PER_DAY = 2
# A position whose market resolved this long ago should have settled.
SETTLE_GRACE_H = 36
# realized_ev is win-or-lose: +(1-p)/p or -100% a position. The 3-SE bar was
# never simulated on that payoff, and it does not hold there: a no-skill
# strategy buying favourites passes 5.5% of the time at 80c, 14.7% at 95c and
# 40.6% at 98c (Phase A review, 2026-09-25), because a run of wins has almost
# no spread. So its evidence is recorded and gate 2 fails, until a bar
# simulated for this payoff is pre-registered in docs/experiments.md.
UNCALIBRATED = ("realized_ev cannot pass gate 2 yet: the 3-SE bar was calibrated"
                " on near-normal values, and on a win-or-lose payoff a no-skill"
                " favourite buyer clears it 5-41% of the time - a bar for this"
                " payoff has to be pre-registered first")


def _preregistered() -> set:
    """Every markdown heading in docs/experiments.md above its Results log,
    outside ``` fences: the pre-registrations, not the results."""
    out, fenced = set(), False
    for ln in EXPERIMENTS.read_text(encoding="utf-8").splitlines():
        if ln.startswith(("```", "~~~")):
            fenced = not fenced
            continue
        m = None if fenced else re.match(r"#{1,6}\s+(.+?)(?:\s+#+)?\s*$", ln)
        if m and m.group(1) == "Results log":
            break
        if m:
            out.add(m.group(1))
    return out


def record_backtest(name: str, experiment: str, passed: bool, reason: str,
                    evidence: dict) -> dict:
    s = strategies.get(name)                         # must be registered
    if experiment.strip() != s.experiment.strip():
        raise ValueError(f"{name} is registered against {s.experiment!r}, not"
                         f" {experiment!r}: gate 1 is the test its own entry"
                         f" pre-registers")
    # The WHOLE heading, not a prefix: "E1" alone would match the unrelated
    # 2026-09-23 "E1. The market's recipe vs reality's recipe".
    if experiment.strip() not in _preregistered():
        raise ValueError(f"{experiment!r} is not a heading above the Results log"
                         f" in docs/experiments.md (the whole heading): pre-register"
                         f" it before recording a result")
    # register() sees only the strategies loaded now; a retired strategy's
    # gate record still holds its entry.
    for other, block in validation._load().items():
        if (other != name and isinstance(block, dict)
                and (block.get("experiment") or "").strip() == experiment.strip()):
            raise ValueError(f"{other}'s gate record already holds {experiment!r}:"
                             f" each strategy needs its own entry")
    return validation.record_backtest(name, experiment, passed, reason, evidence,
                                      metric=s.metric)


def redefined(name: str, block: dict) -> str | None:
    """Why the strategy registered as `name` is not the definition its gate
    record `block` was made for - a different experiment or metric - or
    None when it is the same. Edited in place under the same name, a file
    is a new strategy wearing the old one's gates (re-verification
    2026-09-25: gate 2 re-passed, and gate 3 opened). Fails closed: a name
    no longer registered cannot be shown to be the same."""
    try:
        s = strategies.get(name)
    except KeyError:
        return (f"no strategy named {name!r} is registered, so it cannot be shown"
                f" to be the definition its gate record is for")
    held = ((block.get("experiment") or "").strip(), block.get("metric"))
    if held != (s.experiment.strip(), s.metric):
        return (f"{name} is registered as {s.experiment!r} measured by {s.metric},"
                f" but its gate record is for {held[0]!r} measured by {held[1]}:"
                f" a new definition is a new name")
    return None


def looks_today(con, name: str, now) -> int:
    d = parse_utc(canon_ts(now)).astimezone(ET).date()
    start = dt.datetime(d.year, d.month, d.day, tzinfo=ET)
    return con.execute("SELECT COUNT(*) FROM gate_looks WHERE name=? AND looked_at >= ?"
                       " AND looked_at < ?",
                       (name, canon_ts(start),
                        canon_ts(start + dt.timedelta(days=1)))).fetchone()[0]


def _slot(pos) -> str:
    """A position's slot: its game's start, or its opening if the market
    knows no start."""
    when = parse_utc(pos["event_start"]) or parse_utc(pos["opened_at"])
    return validation.slot_of(when.astimezone(ET).hour)


def measured(con, name: str, metric: str, now) -> dict:
    """The strategy's graded values, placebo values, slots and coverage.

    One value per EVENT (the market's canonical_event_id): the strategy's
    FIRST position on it - the order it placed first - for the strategy and
    its placebo alike. Positions on one game share its move, so a second
    entry is not a second piece of evidence: counted per position, a no-skill
    strategy entering each game k times passed the 3-SE bar 15% (k=2) to 72%
    (k=10) of the time, against 1.8% at k=1, the one-value-per-game shape
    the bar was simulated on. Nor is the mean of a game's positions, when
    how often it re-enters depends on the price: buying again after the
    price fell halves every losing first entry, and a no-skill strategy did
    that past the bar 11 times in 20. The first position was decided before
    the path it is judged on (Phase A re-verification, 2026-09-25).

    If that first position cannot be graded, the game counts against
    coverage; a later, gradable entry does not stand in for it."""
    col = "info" if metric == "info" else "realized_ev"
    firsts = {}
    for mode in ("paper", "placebo"):
        rows = con.execute(
            "SELECT p.*, m.event_start,"
            " COALESCE(m.canonical_event_id, p.market_id) AS event,"
            " COALESCE(o.placed_at, p.opened_at) AS decided"
            " FROM paper_positions p"
            " LEFT JOIN markets m ON m.market_id = p.market_id"
            " LEFT JOIN paper_orders o ON o.order_id = p.order_id"
            " WHERE p.strategy=? AND p.mode=?"
            " ORDER BY decided, p.position_id", (name, mode)).fetchall()
        first = {}
        for r in rows:
            first.setdefault(r["event"], r)
        firsts[mode] = list(first.values())

    # Graded AND settled. The scanner grades once the market resolves and
    # settles separately, so a graded position that has not settled would
    # otherwise stand in for a settled one that could not be graded (review
    # 2026-09-25: gate 2 passed at a true coverage of 17%).
    def counts(p):
        return p[col] is not None and p["result"] is not None
    paper = [p for p in firsts["paper"] if counts(p)]
    settled = con.execute("SELECT COUNT(*) FROM paper_positions WHERE strategy=?"
                          " AND mode='paper' AND result IS NOT NULL", (name,)).fetchone()[0]
    # Coverage: of the games whose first position SHOULD have settled by now,
    # the share whose first position counts - settled, for realized_ev (every
    # settled position has one); graded AND settled, for info. The same unit
    # as the sample, top and bottom: counted per position, re-entering the
    # games that get graded lifted 60 of 100 to "82%". Over positions due, not
    # positions settled, or one that never settles is invisible (it passed at
    # "100%" with 60 of 260 resolved positions graded). A position with no
    # resolves_at cannot be shown not to be due yet, so it counts as due:
    # against coverage until it settles (fails closed).
    due_before = canon_ts(parse_utc(canon_ts(now)) - dt.timedelta(hours=SETTLE_GRACE_H))
    due = [p for p in firsts["paper"]
           if p["resolves_at"] is None or p["resolves_at"] < due_before]
    counted = [p for p in due if counts(p)]
    coverage = len(counted) / len(due) if due else None
    return {"values": [p[col] for p in paper],
            "slots": [_slot(p) for p in paper],
            "placebo": [p[col] for p in firsts["placebo"] if counts(p)],
            "coverage": coverage, "settled": settled}


def score(con, name: str, now) -> dict | None:
    """Re-test one strategy's gate 2. None, with nothing written, when it
    has no gate record yet or has already been looked at twice today.

    It commits, starting with whatever the caller had pending. The look is
    counted and claimed under one write lock, and committed, BEFORE anything
    is evaluated: a busy database, a rollback, a crash or a second scorer
    running at the same moment can only cost a look, never add one."""
    s = strategies.get(name)
    block = validation.status(name)
    if block is None:
        print(f"  {name}: no gate record yet - record its backtest first")
        return None
    con.commit()
    con.execute("BEGIN IMMEDIATE")
    try:
        if looks_today(con, name, now) >= MAX_LOOKS_PER_DAY:
            con.rollback()
            print(f"  {name}: gate 2 already re-tested {MAX_LOOKS_PER_DAY} times today")
            return None
        look = con.execute("INSERT INTO gate_looks (name, looked_at, metric, n, passed,"
                           " reason) VALUES (?,?,?,0,0,'started')",
                           (name, canon_ts(now), s.metric)).lastrowid
        con.commit()
    except BaseException:
        con.rollback()
        raise
    m = measured(con, name, s.metric, now)
    changed = redefined(name, block)
    refuse = [changed] if changed else []
    if s.metric == "realized_ev":
        refuse.append(UNCALIBRATED)
    # record_paper only refuses a placebo that PASSES, so one that placed
    # nothing, or too little to be graded, would block nothing. A sport's
    # placebo is automatic; a strategy's is whatever its author wrote.
    if len(m["placebo"]) < validation.MIN_PAPER_BETS:
        refuse.append(f"the placebo has only {len(m['placebo'])} graded events,"
                      f" need {validation.MIN_PAPER_BETS} - too little evidence to"
                      f" show the gate is measuring the strategy, not a drift")
    r = validation.record_paper(name, m["values"], slots=m["slots"],
                                coverage=m["coverage"], placebo=m["placebo"],
                                metric=s.metric, refuse="; ".join(refuse) or None)
    con.execute("UPDATE gate_looks SET n=?, mean=?, se=?, t=?, coverage=?, passed=?,"
                " reason=? WHERE id=?",
                (r["n_bets"], r["avg_clv"], r["se_clv"], r["t_stat"], r["coverage"],
                 1 if r["passed"] else 0, r["reason"], look))
    con.commit()
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
        # Production's database has none of the scanner's tables until
        # `python db.py`; a free read says so rather than making them.
        have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        ready = {"paper_positions", "gate_looks"} <= have
        if not ready:
            print("the scanner's tables do not exist here yet (`python db.py` makes"
                  " them) - gate 2 is not re-tested; the gate records:")
        now = dt.datetime.now(dt.timezone.utc)
        for name, s in sorted(reg.items()):
            print(f"{name}  ({s.metric}; venues {', '.join(s.venues)};"
                  f" gate 1: {s.experiment})")
            if ready and "--score" in argv:
                r = score(con, name, now)
                con.commit()
                if r:
                    print(f"  gate 2 re-tested: {r['reason']}")
            print(f"  {validation.explain(name) if validation.status(name) else 'no gate record yet'}")
            if ready:
                print(f"  looked at {looks_today(con, name, now)} time(s) today")
    finally:
        con.close()
    return 0
