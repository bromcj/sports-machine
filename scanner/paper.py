"""Paper execution: record what a strategy would do, then simulate what would
have happened - never better than the market actually showed.

  submit()    an intended order: venue, market, outcome, size, limit price,
              time. Refused unless it is paper or placebo, the venue may be
              used, the market has neither started nor resolved, and the
              strategy's daily exposure cap has room.
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
          A maker at or above the ask showing when it is placed is refused:
          it would not rest, it would take.

A size shown is filled once per strategy and mode. Our fills never leave the
recorded book, so an offer still showing at the same price in a later
observation is the same offer, and what this strategy already took from it
is gone (_unused) - until an observation shows a worse price and not that
one. Paper and placebo are separate counterfactuals, and one strategy does
not compete with another.

A sportsbook shows no size, so a book order fills in full or not at all.

FEES, as Kalshi charges them: each fill's cash is rounded up to the cent and
the overpayment rebated across the order's fills, so an order has always paid
its whole cash so far rounded up once. A fill that would still take its stake
past the exposure the daily cap counted is not made.

Nothing fills at or after a game's start. The Odds API keeps quoting in-play
prices, and a pregame order must not be filled at one: fair_value refuses
them, so the position would be graded against a different market state. An
order still open at the start expires.

UNITS. An exchange order's size is contracts; a book order's size is dollars
staked. Both are held as contracts paying $1 if they win - a $100 stake at
-110 is 190.9 contracts at 0.5238 - so one set of arithmetic serves both.

There is no code path here, or anywhere, that sends an order to a venue.
paper_orders.mode is CHECKed to paper/placebo by the database itself.
"""
import datetime as dt
import math
import numbers
from decimal import ROUND_CEILING, Decimal

import config
from feeds import ET, parse_utc
from scanner import capital, fees
from scanner.fair import fair_value
from scanner.store import canon_ts, next_quotes_after
from scanner.venues import family, paper_allowed

MODES = ("paper", "placebo")
ROLES = ("taker", "maker")
OPEN = ("open", "partial_open")
_CENT = Decimal("0.01")


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


def _fee_model(con, market_id: str, now: str) -> str:
    """The fee model on the market's latest price at or before `now`: a
    schedule first seen later cannot decide an earlier order."""
    row = con.execute("SELECT fee_model FROM prices WHERE market_id=? AND captured_at <= ?"
                      " ORDER BY captured_at DESC LIMIT 1", (market_id, now)).fetchone()
    if row is None:
        raise Refused(f"no price had been seen for this market by {now}")
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
    if role == "taker" and expires_at is not None:
        raise Refused("a taker order does not rest - it takes the next observation"
                      " or is cancelled - so it cannot carry an expires_at")
    if not (isinstance(size, numbers.Real) and math.isfinite(size) and size > 0):
        raise Refused(f"size must be a finite positive number, got {size!r}")
    if not (isinstance(limit_price, numbers.Real) and 0 < limit_price < 1):
        raise Refused(f"limit price {limit_price!r} is not a probability in (0, 1)")
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
    # Too late: nothing fills at or after a game's start, and a market with
    # no start (weather, economics) settles at resolves_at.
    if mkt["event_start"] and now >= canon_ts(mkt["event_start"]):
        raise Refused(f"the market started at {mkt['event_start']}: nothing fills"
                      " at or after the start")
    if mkt["resolves_at"] and now >= canon_ts(mkt["resolves_at"]):
        raise Refused(f"the market resolved at {mkt['resolves_at']}")
    if role == "maker":
        # A limit at or through the ask showing now would not rest: the venue
        # matches it at once, at the ask, with the taker fee (or cancels it,
        # if post-only). Only what was captured by `now` is read.
        ask = con.execute(
            "SELECT price, captured_at FROM prices WHERE market_id=? AND outcome=?"
            " AND quote='ask' AND level=1 AND captured_at <= ?"
            " ORDER BY captured_at DESC LIMIT 1", (market_id, outcome, now)).fetchone()
        if ask is not None and limit_price >= ask["price"] - 1e-12:
            raise Refused(f"a maker at {limit_price} would cross the {ask['price']} ask"
                          f" showing at {ask['captured_at']}; it would take, so place"
                          " it as a taker")
    model = _fee_model(con, market_id, now)
    contracts = _contracts(is_book, size, limit_price)
    try:
        worst = contracts * limit_price + fees.fee(model, limit_price, contracts, role)
    except fees.UnknownFee as e:
        raise Refused(f"cannot price this order's fees: {e}")
    except ArithmeticError as e:             # a size too big for Decimal to hold
        raise Refused(f"cannot price this order's fees at size {size}:"
                      f" {type(e).__name__}")
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

def _dec(x) -> Decimal:
    return Decimal(str(x))


def _fill(con, order, price_row, contracts: float, price: float, role: str) -> bool:
    """Record one fill. Returns False, and records nothing, when it would take
    the order's stake past the exposure its daily cap counted: that is hard.

    Kalshi rounds each fill's cash up to the cent, then rebates the
    overpayment across the order's fills (docs/venues.md). So once this fill
    is in, the order has paid its whole cash so far rounded up to the cent
    once, and this fill's fee is that less what the earlier fills paid - it
    can be a small rebate. Rounded per fill with no rebate, 100 one-contract
    fills at 0.405 staked 41.00 against an exposure of 40.50."""
    model = price_row["fee_model"]
    prior = con.execute("SELECT f.contracts, f.price, f.fee, p.fee_model FROM paper_fills f"
                        " JOIN prices p ON p.price_id=f.price_id WHERE f.order_id=?",
                        (order["order_id"],)).fetchall()
    paid = sum(_dec(f["contracts"]) * _dec(f["price"]) + _dec(f["fee"]) for f in prior)
    cost = _dec(contracts) * _dec(price)
    if model.startswith("kalshi:"):
        cash = sum(_dec(c) * _dec(p) + _dec(fees.fee(m, p, c, role, conservative=False))
                   for c, p, m in [*((f["contracts"], f["price"], f["fee_model"])
                                     for f in prior), (contracts, price, model)])
        fee = float(cash.quantize(_CENT, rounding=ROUND_CEILING) - paid - cost)
    else:
        fee = fees.fee(model, price, contracts, role)
    if float(paid + cost) + fee > order["exposure"] + 1e-9:
        return False
    con.execute("INSERT INTO paper_fills (order_id, price_id, filled_at, contracts,"
                " price, fee) VALUES (?,?,?,?,?,?)",
                (order["order_id"], price_row["price_id"], price_row["captured_at"],
                 contracts, price, fee))
    return True


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


def _unused(con, o, q) -> float:
    """What level `q` still shows for this order. Our fills never leave the
    recorded book, so an offer still showing at the same price in later
    observations is the same offer: take off what this strategy's orders in
    this mode already took at that price, here and back to the last
    observation that showed a worse price but not this one. Only the top
    levels are stored, so an observation with nothing worse cannot tell
    "gone" from "pushed below the levels recorded" - it is not a gap. That
    can under-fill, never over-fill. A size is filled once per strategy and
    mode - paper and placebo are separate counterfactuals, and one strategy
    does not compete with another."""
    gap = con.execute(
        "SELECT a.captured_at FROM prices a WHERE a.market_id=? AND a.outcome=?"
        " AND a.quote='ask' AND a.captured_at < ? AND a.price > ? + 1e-9"
        " AND NOT EXISTS (SELECT 1 FROM"
        " prices b WHERE b.market_id=a.market_id AND b.outcome=a.outcome AND"
        " b.quote='ask' AND b.captured_at=a.captured_at AND ABS(b.price - ?) < 1e-9)"
        " ORDER BY a.captured_at DESC LIMIT 1",
        (q["market_id"], q["outcome"], q["captured_at"], q["price"],
         q["price"])).fetchone()
    used = con.execute(
        "SELECT COALESCE(SUM(f.contracts), 0) FROM paper_fills f"
        " JOIN paper_orders o ON o.order_id=f.order_id"
        " JOIN prices p ON p.price_id=f.price_id"
        " WHERE o.strategy=? AND o.mode=? AND p.market_id=? AND p.outcome=?"
        " AND p.quote='ask' AND ABS(p.price - ?) < 1e-9"
        " AND p.captured_at > ? AND p.captured_at <= ?",
        (o["strategy"], o["mode"], q["market_id"], q["outcome"], q["price"],
         gap[0] if gap else "", q["captured_at"])).fetchone()[0]
    return max(0.0, (q["size_available"] or 0.0) - float(used))


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
        start = _start(con, o["market_id"])
        if o["role"] == "taker":
            quotes = next_quotes_after(con, o["market_id"], o["outcome"], "ask",
                                       o["placed_at"])
            started = start is not None and now >= start
            if (not quotes or quotes[0]["captured_at"] > now) and not started:
                tally["waiting"] += 1
                continue
            if start is not None and (not quotes or quotes[0]["captured_at"] >= start):
                quotes = []                      # the next price is in play: no fill
            left = target
            for q in quotes:
                if q["price"] > o["limit_price"] + 1e-12 or left <= 1e-9:
                    break
                if is_book:
                    take = o["size"] / q["price"]   # the stake, at the price shown
                else:
                    take = min(left, _unused(con, o, q))
                if take > 0:
                    if not _fill(con, o, q, take, q["price"], "taker"):
                        break                       # past its exposure: see _fill
                    tally["fills"] += 1
                left = 0 if is_book else left - take
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


def _start(con, market_id: str) -> str | None:
    row = con.execute("SELECT event_start FROM markets WHERE market_id=?",
                      (market_id,)).fetchone()
    return row["event_start"] if row else None


def _simulate_maker(con, o, target: float, now: str, tally: dict) -> str:
    """Walk every observation after the order (and after the last one used)."""
    after = _last_seen(con, o["order_id"]) or o["placed_at"]
    start = _start(con, o["market_id"])
    expires = min(x for x in (o["expires_at"], start) if x) if (o["expires_at"] or start) else None
    end = min(now, expires) if expires else now
    got = _filled_so_far(con, o["order_id"])
    while got < target - 1e-9:
        quotes = next_quotes_after(con, o["market_id"], o["outcome"], "ask", after)
        # The second condition cannot happen while next_quotes_after is
        # strictly-after; if that ever broke, this loop would spin forever
        # on one observation (it did, when that rule was broken on purpose
        # to test the tests). Stop instead.
        if (not quotes or quotes[0]["captured_at"] > end
                or quotes[0]["captured_at"] <= after
                or (start is not None and quotes[0]["captured_at"] >= start)):
            break
        after = quotes[0]["captured_at"]
        through = [q for q in quotes if q["price"] < o["limit_price"] - 1e-12]
        for q in through:
            take = min(target - got, _unused(con, o, q))
            if take > 0 and _fill(con, o, q, take, o["limit_price"], "maker"):
                tally["fills"] += 1
                got += take
    if got >= target - 1e-9:
        _refresh_position(con, o["order_id"])
        return "filled"
    if expires and now >= expires:
        _refresh_position(con, o["order_id"])
        return "partial" if got > 0 else "expired"
    if got > 0:
        _refresh_position(con, o["order_id"])
    return "partial_open" if got > 0 else "open"


# ---------------------------------------------------------------- grading ---

def grade(con, position_id: int, when) -> dict:
    """The fair close against the fair price at entry: `info`, in percent.

    Graded only when (pre-registered, docs/experiments.md S0):
      - the market has a start time, and `when` is at or after it, so the
        close exists. Before the start the newest pull is only the latest
        price so far (and, in a replay, a later one is already stored);
        a graded position is never graded again, so it waits;
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
    if canon_ts(when) < canon_ts(m["event_start"]):
        return {"graded": False, "why": "not started yet, so the close is not in"}
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
