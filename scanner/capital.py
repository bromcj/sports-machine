"""Capital accounting: what a position ties up, for how long, and what that
edge is worth per year.

    annualized_ev = ev / days_to_resolution x 365

A 5% edge on a game that settles tonight and a 5% edge on a contract that
settles in eight months are not the same thing: the second locks the money up
240 times longer. Every report shows both numbers, and the total capital locked
up, by strategy and by the month it comes back.

This is simple (not compounded) annualization, as the brief specifies. For a
game settling in hours it produces very large numbers; that is the honest
reading of "this money is back tonight", not a forecast of a yearly return.
"""
import datetime as dt

from feeds import ET, parse_utc


def days_between(start, end) -> float | None:
    a, b = parse_utc(start), parse_utc(end)
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 86400


def annualize(ev: float | None, days: float | None) -> float | None:
    """ev per dollar over `days` -> per year. None when undefined."""
    if ev is None or days is None or days <= 0:
        return None
    return ev / days * 365


def resolution_month(resolves_at) -> str:
    when = parse_utc(resolves_at)
    return when.astimezone(ET).strftime("%Y-%m") if when else "unknown"


def locked(con, mode: str = "paper") -> dict:
    """Capital in open (unsettled) positions: total, by strategy, by the ET
    month it resolves in, and by venue."""
    out = {"total": 0.0, "by_strategy": {}, "by_month": {}, "by_venue": {}}
    for r in con.execute("SELECT strategy, venue, stake, resolves_at FROM paper_positions"
                         " WHERE mode=? AND result IS NULL", (mode,)):
        out["total"] += r["stake"]
        for key, k in (("by_strategy", r["strategy"]),
                       ("by_month", resolution_month(r["resolves_at"])),
                       ("by_venue", r["venue"])):
            out[key][k] = out[key].get(k, 0.0) + r["stake"]
    return out
