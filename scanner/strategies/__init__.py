"""The strategy registry. Adding a strategy is a file, not a rewrite.

A strategy is a module in this folder that calls register(Strategy(...)).
load() imports every module here, so a new file is picked up by existing code.
Phase A ships the registry with no strategies in it: they come in B and E,
each behind its own pre-registration and its own gates.

A Strategy is:

  name        lowercase, unique, not a sport's name - it is the strategy's key
              in validation.json and what a person passes to arm()
  venues      the venue families (or exact venues) it may trade
  signal      signal(con, now) -> [Intent]: what it would do right now
  placebo     placebo(con, intent, now) -> Intent | None: the same pipeline
              making a random choice. REQUIRED: gate 2 refuses a strategy
              whose placebo also passes, and one whose placebo has fewer
              than 50 graded positions, so a strategy whose placebo places
              nothing can never pass.
  metric      'info' or 'realized_ev' - what its gate 2 measures (S0)
  experiment  the whole heading of its entry in docs/experiments.md - its
              own: no two strategies may share one
  execute     execute(con, intent, now, mode) -> order_id. Default: a paper
              order through scanner.paper.submit
  grade       grade(con, position, now) -> None. Default: scanner.paper.grade
              (the fair close); settling a result is venue-specific, so a
              strategy that settles its own positions supplies this

run() is one pass: signals, paper orders and their placebos, fills, grading.
"""
import dataclasses
import importlib
import pkgutil
import re
from typing import Callable

import config

METRICS = ("info", "realized_ev")
NAME = re.compile(r"^[a-z][a-z0-9_]{1,31}$")

REGISTRY: dict = {}


@dataclasses.dataclass(frozen=True)
class Intent:
    market_id: str
    outcome: str
    role: str
    size: float
    limit_price: float
    expires_at: str | None = None
    note: str | None = None


def _default_execute(con, intent: Intent, now, mode: str, strategy: str):
    from scanner import paper
    return paper.submit(con, strategy=strategy, mode=mode,
                        market_id=intent.market_id, outcome=intent.outcome,
                        role=intent.role, size=intent.size,
                        limit_price=intent.limit_price, now=now,
                        expires_at=intent.expires_at, note=intent.note)


def _default_grade(con, position, now):
    from scanner import paper
    return paper.grade(con, position["position_id"], now)


@dataclasses.dataclass(frozen=True)
class Strategy:
    name: str
    venues: tuple
    signal: Callable
    placebo: Callable
    metric: str
    experiment: str
    description: str = ""
    execute: Callable | None = None
    grade: Callable | None = None


def register(s: Strategy) -> Strategy:
    from scanner.venues import VENUES, family
    if not NAME.match(s.name or ""):
        raise ValueError(f"strategy name {s.name!r}: lowercase letters, digits, _")
    if s.name in config.SPORTS:
        raise ValueError(f"{s.name!r} is a sport; a strategy needs its own name")
    if s.name in REGISTRY and REGISTRY[s.name] is not s:
        raise ValueError(f"a strategy named {s.name!r} is already registered")
    if s.metric not in METRICS:
        raise ValueError(f"metric must be one of {METRICS}, got {s.metric!r}")
    if not callable(s.signal) or not callable(s.placebo):
        raise ValueError(f"{s.name}: a signal and a placebo are both required")
    if not s.experiment:
        raise ValueError(f"{s.name}: name its entry in docs/experiments.md")
    for other in REGISTRY.values():
        if other.name != s.name and other.experiment.strip() == s.experiment.strip():
            raise ValueError(f"{s.name}: {other.name} is already registered against"
                             f" {s.experiment!r} - each strategy needs its own entry")
    if not s.venues:
        raise ValueError(f"{s.name}: which venues may it trade?")
    for v in s.venues:
        if v not in VENUES:
            family(v)                           # raises on an unknown venue
    REGISTRY[s.name] = s
    return s


def load() -> dict:
    """Import every strategy module in this folder. Returns the registry."""
    for mod in pkgutil.iter_modules(__path__):
        importlib.import_module(f"{__name__}.{mod.name}")
    return REGISTRY


def get(name: str) -> Strategy:
    load()
    if name not in REGISTRY:
        raise KeyError(f"no strategy named {name!r}")
    return REGISTRY[name]


def trades_at(s: Strategy, venue: str) -> bool:
    return venue in s.venues or venue.split(":", 1)[0] in s.venues


def run(con, s: Strategy, now) -> dict:
    """One pass of one strategy, all paper. The caller commits."""
    from scanner import paper
    execute = s.execute or (lambda c, i, t, m: _default_execute(c, i, t, m, s.name))
    grade = s.grade or _default_grade
    out = {"intents": 0, "orders": 0, "placebos": 0, "refused": [], "graded": 0}
    for intent in s.signal(con, now) or []:
        out["intents"] += 1
        mkt = con.execute("SELECT venue FROM markets WHERE market_id=?",
                          (intent.market_id,)).fetchone()
        if mkt is None or not trades_at(s, mkt["venue"]):
            out["refused"].append(f"{intent.market_id}: not a venue {s.name} trades")
            continue
        try:
            execute(con, intent, now, "paper")
            out["orders"] += 1
        except paper.Refused as e:
            out["refused"].append(str(e))
            continue
        twin = s.placebo(con, intent, now)
        if twin is not None:
            try:
                execute(con, twin, now, "placebo")
                out["placebos"] += 1
            except paper.Refused as e:
                out["refused"].append(f"placebo: {e}")
    out["fills"] = paper.simulate(con, now)
    for pos in con.execute("SELECT * FROM paper_positions WHERE strategy=?"
                           " AND graded_at IS NULL", (s.name,)).fetchall():
        r = grade(con, pos, now)
        out["graded"] += bool(r and r.get("graded"))
    return out
