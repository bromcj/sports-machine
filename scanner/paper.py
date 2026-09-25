"""Paper execution: record what a strategy would do, then simulate what would
have happened - never better than the market actually showed.

  submit()    an intended order: venue, market, outcome, size, limit price,
              time. Refused unless it is paper or placebo, the venue may be
              used, and the strategy's daily exposure cap has room.
  simulate()  fills open orders against prices observed AFTER they were
              placed. Never the price showing when the order went in.
  settle()    a result for a position, and its profit.

THE FILL RULES (pre-registered in docs/experiments.md, section S0):

  taker   fills against the FIRST observation strictly after placed_at, and
          only that one: walk its levels best first, take each while the
          price is at or under the limit, never more than the size shown. What
          is left is cancelled - a taker order does not rest.
  maker   rests until expires_at. Fills only when a later observation shows
          the market trading THROUGH its price - the best ask strictly below
          the bid - at the order's own price, never more than the size shown.
          Touching the price is not enough: others may be ahead in the queue.

A sportsbook shows no size, so a book order fills in full or not at all.

UNITS. An exchange order's size is contracts; a book order's size is dollars
staked. Both are held as contracts paying $1 if they win - a $100 stake at
-110 is 190.9 contracts at 0.5238 - so one set of arithmetic serves both.

There is no code path here, or anywhere, that sends an order to a venue.
paper_orders.mode is CHECKed to paper/placebo by the database itself.
"""
import datetime as dt

import config
from feeds import ET, parse_utc
from scanner import capital, fees
from scanner.fair import fair_value
from scanner.store import canon_ts, next_quotes_after
from scanner.venues import family, paper_allowed

MODES = ("paper", "placebo")
ROLES = ("taker", "maker")
OPEN = ("open", "partial_open")


class Refused(ValueError):
    """The order was not recorded, and why."""


def daily_cap(strategy: str) -> float:
    caps = config.STRATEGY_DAILY_EXPOSURE
    return float(caps.get(strategy, caps["default"]))


def exposure_today(con, strategy: str, mode: str, now) -> float:
    """Worst-case loss already committed today (ET) by this strategy."""
    d = parse_utc(canon_ts(now)).astimezone(ET).date()
    start = dt.datetime(d.year, d.month, d.day, tzinfo=ET)
    end = start + dt.timedelta(days=1)
    return float(con.execute(
        "SELECT COALESCE(SUM(exposure), 0) FROM paper_orders WHERE strategy=?"
        " AND mode=? AND placed_at >= ? AND placed_at < ?",
        (strategy, mode, canon_ts(start), canon_ts(end))).fetchone()[0])


def _fee_model(con, market_id: str) -> str:
    row = con.execute("SELECT fee_model FROM prices WHERE market_id=?"
                      " ORDER BY captured_at DESC LIMIT 1", (market_id,)).fetchone()
    if row is None:
        raise Refused("no price has ever been seen for this market")
    return row["fee_model"]


def _contracts(is_book: bool, size: float, price: float) -> float:
    return size / price if is_book else size


def submit(con, *, strategy: str, mode: str, market_id: str, outcome: str,
           role: str, size: float, limit_price: float, now, expires_at=None,
           note: str | None = None) -> int:
    """Record an intended paper order. Returns order_id or raises Refused."""
    if mode not in MODES:
        raise Refused(f"mode must be paper or placebo, not {mode!r}")
    if role not in ROLES:
        raise Refused(f"role must be taker or maker, not {role!r}")
    if not (size > 0):
        raise Refused(f"size must be positive, got {size}")
    if not (0 < limit_price < 1):
        raise Refused(f"limit price {limit_price} is not a probability in (0, 1)")
    mkt = con.execute("SELECT * FROM markets WHERE market_id=?", (market_id,)).fetchone()
    if mkt is None:
        raise Refused(f"unknown market {market_id!r}")
    ok, why = paper_allowed(mkt["venue"], mkt["sport"])
    if not ok:
        raise Refused(why)
    is_book = family(mkt["venue"]) == "sportsbook"
    if is_book and role != "taker":
        raise Refused("a sportsbook order can only take the posted price")
    now = canon_ts(now)
    model = _fee_model(con, market_id)
    contracts = _contracts(is_book, size, limit_price)
    try:
        worst = contracts * limit_price + fees.fee(model, limit_price, contracts, role)
    except fees.UnknownFee as e:
        raise Refused(f"cannot price this order's fees: {e}")
    room = daily_cap(strategy) - exposure_today(con, strategy, mode, now)
    if worst > room + 1e-9:
        raise Refused(f"daily exposure cap: {worst:.2f} needed, {max(room, 0):.2f} of "
                      f"{daily_cap(strategy):.2f} left for {strategy} today")
    fv = fair_value(con, market_id, outcome, now)
    ev = None if fv is None else fees.ev(fv["p"], model, limit_price, contracts, role)
    if role == "maker" and expires_at is None:
        expires_at = mkt["event_start"] or canon_ts(parse_utc(now) + dt.timedelta(days=1))
    cur = con.execute(
        "INSERT INTO paper_orders (strategy, mode, venue, market_id, outcome, role,"
        " size, limit_price, placed_at, expires_at, exposure, fair_p, fair_source,"
        " fair_as_of, ev_at_order, status, note)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'open',?)",
        (strategy, mode, mkt["venue"], market_id, outcome, role, float(size),
         float(limit_price), now, None if expires_at is None else canon_ts(expires_at),
         round(worst, 6), None if fv is None else fv["p"],
         None if fv is None else fv["source"], None if fv is None else fv["as_of"],
         ev, note))
    return cur.lastrowid


# ------------------------------------------------------------------ fills ---

def _fill(con, order, price_row, contracts: float, price: float, role: str):
    fee = fees.fee(price_row["fee_model"], price, contracts, role)
    con.execute("INSERT INTO paper_fills (order_id, price_id, filled_at, contracts,"
                " price, fee) VALUES (?,?,?,?,?,?)",
                (order["order_id"], price_row["price_id"], price_row["captured_at"],
                 contracts, price, fee))


def _refresh_position(con, order_id: int):
    """Rebuild the position from its fills. Capital accounting lives here."""
    o = con.execute("SELECT * FROM paper_orders WHERE order_id=?", (order_id,)).fetchone()
    fills = con.execute("SELECT * FROM paper_fills WHERE order_id=? ORDER BY filled_at,"
                        " fill_id", (order_id,)).fetchall()
    if not fills:
        return
    n = sum(f["contracts"] for f in fills)
    cost = sum(f["contracts"] * f["price"] for f in fills)
    fee = sum(f["fee"] for f in fills)
    stake = cost + fee
    opened = fills[0]["filled_at"]
    mkt = con.execute("SELECT resolves_at FROM markets WHERE market_id=?",
                      (o["market_id"],)).fetchone()
    resolves = mkt["resolves_at"] if mkt else None
    days = capital.days_between(opened, resolves)
    fv = fair_value(con, o["market_id"], o["outcome"], opened)
    ev = None if fv is None else fv["p"] / (stake / n) - 1
    con.execute(
        "INSERT INTO paper_positions (order_id, strategy, mode, venue, market_id,"
        " outcome, contracts, avg_price, fee, stake, opened_at, resolves_at,"
        " days_to_resolution, fair_p, fair_source, ev, annualized_ev)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(order_id) DO UPDATE SET contracts=excluded.contracts,"
        "  avg_price=excluded.avg_price, fee=excluded.fee, stake=excluded.stake,"
        "  days_to_resolution=excluded.days_to_resolution, ev=excluded.ev,"
        "  annualized_ev=excluded.annualized_ev",
        (order_id, o["strategy"], o["mode"], o["venue"], o["market_id"], o["outcome"],
         n, cost / n, fee, stake, opened, resolves, days,
         None if fv is None else fv["p"], None if fv is None else fv["source"],
         ev, capital.annualize(ev, days)))


def _filled_so_far(con, order_id: int) -> float:
    return float(con.execute("SELECT COALESCE(SUM(contracts), 0) FROM paper_fills"
                             " WHERE order_id=?", (order_id,)).fetchone()[0])


def _last_seen(con, order_id: int):
    return con.execute("SELECT MAX(filled_at) FROM paper_fills WHERE order_id=?",
                       (order_id,)).fetchone()[0]


def simulate(con, now) -> dict:
    """Fill what the market would have filled, up to `now`. Returns counts."""
    now = canon_ts(now)
    tally = {"filled": 0, "partial": 0, "expired": 0, "waiting": 0, "fills": 0}
    orders = con.execute("SELECT * FROM paper_orders WHERE status IN ('open',"
                         " 'partial_open') AND placed_at < ? ORDER BY order_id",
                         (now,)).fetchall()
    for o in orders:
        is_book = family(o["venue"]) == "sportsbook"
        target = _contracts(is_book, o["size"], o["limit_price"])
        if o["role"] == "taker":
            quotes = next_quotes_after(con, o["market_id"], o["outcome"], "ask",
                                       o["placed_at"])
            if not quotes or quotes[0]["captured_at"] > now:
                tally["waiting"] += 1
                continue
            left = target
            for q in quotes:
                if q["price"] > o["limit_price"] + 1e-12 or left <= 1e-9:
                    break
                if is_book:
                    take = o["size"] / q["price"]   # the stake, at the price shown
                    left = 0
                else:
                    take = min(left, q["size_available"] or 0.0)
                    left -= take
                if take > 0:
                    _fill(con, o, q, take, q["price"], "taker")
                    tally["fills"] += 1
            got = _filled_so_far(con, o["order_id"])
            status = ("expired" if got == 0 else
                      "filled" if left <= 1e-9 else "partial")
        else:
            status = _simulate_maker(con, o, target, now, tally)
            if status in OPEN:
                tally["waiting"] += 1
                if status != o["status"]:
                    con.execute("UPDATE paper_orders SET status=? WHERE order_id=?",
                                (status, o["order_id"]))
                continue
        con.execute("UPDATE paper_orders SET status=? WHERE order_id=?",
                    (status, o["order_id"]))
        tally[status] += 1
        _refresh_position(con, o["order_id"])
    return tally


def _simulate_maker(con, o, target: float, now: str, tally: dict) -> str:
    """Walk every observation after the order (and after the last one used)."""
    after = _last_seen(con, o["order_id"]) or o["placed_at"]
    end = min(now, o["expires_at"]) if o["expires_at"] else now
    got = _filled_so_far(con, o["order_id"])
    while got < target - 1e-9:
        quotes = next_quotes_after(con, o["market_id"], o["outcome"], "ask", after)
        # The second condition cannot happen while next_quotes_after is
        # strictly-after; if that ever broke, this loop would spin forever
        # on one observation (it did, when that rule was broken on purpose
        # to test the tests). Stop instead.
        if not quotes or quotes[0]["captured_at"] > end or quotes[0]["captured_at"] <= after:
            break
        after = quotes[0]["captured_at"]
        through = [q for q in quotes if q["price"] < o["limit_price"] - 1e-12]
        for q in through:
            take = min(target - got, q["size_available"] or 0.0)
            if take > 0:
                _fill(con, o, q, take, o["limit_price"], "maker")
                tally["fills"] += 1
                got += take
    if got >= target - 1e-9:
        _refresh_position(con, o["order_id"])
        return "filled"
    if o["expires_at"] and now >= o["expires_at"]:
        _refresh_position(con, o["order_id"])
        return "partial" if got > 0 else "expired"
    if got > 0:
        _refresh_position(con, o["order_id"])
    return "partial_open" if got > 0 else "open"


# ---------------------------------------------------------------- grading ---

def grade(con, position_id: int, when) -> dict:
    """The fair close against the fair price at entry: `info`, in percent.

    Graded only when (pre-registered, docs/experiments.md S0):
      - the market has a start time, so a close exists;
      - a fair close was captured within CLOSING_WINDOW_MIN of the start
        (bets/log.py's constant - the sport model's window);
      - entry and close come from the SAME fair source. The 2026-09-24
        review found the MLB gate mixing consensus at entry with Pinnacle at
        the close on every graded bet; a position like that is left ungraded
        here, and so counts against coverage.
    """
    from bets.log import CLOSING_WINDOW_MIN
    p = con.execute("SELECT * FROM paper_positions WHERE position_id=?",
                    (position_id,)).fetchone()
    m = con.execute("SELECT * FROM markets WHERE market_id=?",
                    (p["market_id"],)).fetchone()
    if m is None or not m["event_start"]:
        return {"graded": False, "why": "no start time, so no closing price"}
    close = fair_value(con, p["market_id"], p["outcome"], m["event_start"])
    entry = fair_value(con, p["market_id"], p["outcome"], p["opened_at"])
    if close is None or entry is None:
        return {"graded": False, "why": "no fair price at entry or at the close"}
    early = capital.days_between(close["as_of"], m["event_start"]) * 1440
    if early > CLOSING_WINDOW_MIN:
        return {"graded": False,
                "why": f"nearest close {early:.0f} min before the start"
                       f" (window {CLOSING_WINDOW_MIN})"}
    if close["source"] != entry["source"]:
        return {"graded": False,
                "why": f"fair source changed: {entry['source']} -> {close['source']}"}
    info = (close["p"] / entry["p"] - 1) * 100
    con.execute("UPDATE paper_positions SET fair_close_p=?, fair_close_source=?,"
                " info=?, graded_at=? WHERE position_id=?",
                (close["p"], close["source"], info, canon_ts(when), position_id))
    return {"graded": True, "info": info}


# ------------------------------------------------------------- settlement ---

def settle(con, position_id: int, result: str, when) -> float:
    """Record a position's result. win pays $1 a contract; push and void
    return the stake. Returns the profit."""
    if result not in ("win", "loss", "push", "void"):
        raise ValueError(f"result must be win, loss, push or void, not {result!r}")
    p = con.execute("SELECT * FROM paper_positions WHERE position_id=?",
                    (position_id,)).fetchone()
    pnl = {"win": p["contracts"] - p["stake"], "loss": -p["stake"]}.get(result, 0.0)
    con.execute("UPDATE paper_positions SET result=?, pnl=?, settled_at=?,"
                " realized_ev=? WHERE position_id=?",
                (result, pnl, canon_ts(when),
                 (pnl / p["stake"] * 100) if p["stake"] else None, position_id))
    return pnl
