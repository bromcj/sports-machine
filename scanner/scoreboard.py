"""The scanner's scoreboard: per strategy, per venue, and in total.

    python run_daily.py scoreboard      your own bets first, then this

For every group: orders and what became of them, positions open and settled,
EV at the fill (raw, and annualized so a long contract shows what it costs in
time), profit so far, and the capital still locked - by strategy and by the
month it comes back. Paper and placebo are counted apart; a strategy's gate
verdict is its own line from validation.json. Reads only. Free.
"""
from model import validation
from scanner import capital


def _stats(rows) -> dict:
    evs = [r["ev"] for r in rows if r["ev"] is not None]
    ann = [r["annualized_ev"] for r in rows if r["annualized_ev"] is not None]
    settled = [r for r in rows if r["result"] is not None]
    staked = sum(r["stake"] for r in settled)
    pnl = sum(r["pnl"] or 0 for r in settled)
    return {"positions": len(rows),
            "open": sum(1 for r in rows if r["result"] is None),
            "settled": len(settled),
            "graded": sum(1 for r in rows if r["info"] is not None),
            "ev": sum(evs) / len(evs) if evs else None,
            "annualized": sum(ann) / len(ann) if ann else None,
            "locked": sum(r["stake"] for r in rows if r["result"] is None),
            "pnl": pnl, "roi": (pnl / staked) if staked else None}


def summary(con) -> dict:
    have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "paper_positions" not in have:
        return {"ready": False}
    orders = {}
    for r in con.execute("SELECT strategy, mode, status, COUNT(*) n FROM paper_orders"
                         " GROUP BY 1, 2, 3"):
        orders.setdefault((r["strategy"], r["mode"]), {})[r["status"]] = r["n"]
    rows = con.execute("SELECT * FROM paper_positions").fetchall()
    out = {"ready": True, "by_strategy": {}, "by_venue": {}, "orders": orders}
    for key, pick in (("by_strategy", lambda r: (r["strategy"], r["mode"])),
                      ("by_venue", lambda r: (r["venue"], r["mode"]))):
        groups = {}
        for r in rows:
            groups.setdefault(pick(r), []).append(r)
        out[key] = {k: _stats(v) for k, v in sorted(groups.items())}
    out["total"] = {m: _stats([r for r in rows if r["mode"] == m])
                    for m in ("paper", "placebo")}
    out["locked"] = capital.locked(con)
    names = sorted({k[0] for k in out["by_strategy"]} | {k[0] for k in orders})
    out["verdicts"] = {n: (validation.explain(n) if validation.status(n)
                           else f"{n}: no gate record yet") for n in names}
    return out


def _pct(x):
    return "     -" if x is None else f"{100 * x:+6.1f}%"


def report(con, out=print) -> int:
    s = summary(con)
    out("\n" + "=" * 74)
    out("SCANNER - paper only, per strategy, per venue, in total")
    out("=" * 74)
    if not s["ready"]:
        out("\n  the scanner's tables do not exist here yet (`python db.py` makes them)")
        return 0
    if not s["by_strategy"] and not s["orders"]:
        out("\n  no strategy has placed a paper order yet")
        return 0
    # Wide enough for the longest label: cut to a fixed width, a long name's
    # paper and placebo rows printed identically.
    w = max([26] + [len(f"{n} [{m}]") for k in ("by_strategy", "by_venue") for n, m in s[k]])
    head = (f"  {'':{w}s} {'pos':>4s} {'open':>5s} {'settled':>7s} {'EV':>7s}"
            f" {'EV/yr':>8s} {'locked':>9s} {'P&L':>9s}")
    for title, key in (("by strategy", "by_strategy"), ("by venue", "by_venue")):
        out(f"\n{title}\n{head}")
        for (name, mode), st in s[key].items():
            label = f"{name} [{mode}]"
            out(f"  {label:{w}s} {st['positions']:>4d} {st['open']:>5d}"
                f" {st['settled']:>7d} {_pct(st['ev'])} {_pct(st['annualized']):>8s}"
                f" {st['locked']:>9.2f} {st['pnl']:>+9.2f}")
    out(f"\nin total\n{head}")
    for mode, st in s["total"].items():
        out(f"  {mode:{w}s} {st['positions']:>4d} {st['open']:>5d} {st['settled']:>7d}"
            f" {_pct(st['ev'])} {_pct(st['annualized']):>8s} {st['locked']:>9.2f}"
            f" {st['pnl']:>+9.2f}")
    lk = s["locked"]
    out(f"\ncapital locked in open paper positions: {lk['total']:.2f}")
    for month, amt in sorted(lk["by_month"].items()):
        out(f"  comes back {month}: {amt:.2f}")
    if s["orders"]:
        out("\norders")
        for (name, mode), st in sorted(s["orders"].items()):
            out(f"  {name} [{mode}]: " + ", ".join(f"{k} {v}" for k, v in sorted(st.items())))
    out("\ngates")
    for line in s["verdicts"].values():
        out(f"  {line}")
    out("\nEV is at the fill, after fees, against fair_value. EV/yr divides it by"
        "\nthe days until the money comes back, times 365 - large for a game"
        "\nsettling tonight, honest for a contract settling in eight months.")
    return 0
