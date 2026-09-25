"""What trading at a price costs, on each venue. Every EV is after fees.

Each price row carries a fee-model key, so any EV can be traced to the rule
that produced it:

  book                     a sportsbook. No separate fee: the margin is
                           inside the price, so EV = p x decimal - 1.
  kalshi:<type>:<mult>     Kalshi. `type` and `mult` are the series object's
                           fee_type and fee_multiplier (GET /series/{ticker}),
                           so a series with its own schedule carries it.
  polymarket:<rate>        Polymarket, at the taker rate for the market's
                           category.

An unknown or unread model raises UnknownFee, and so does a multiplier or
rate that is NaN, infinite or negative. A strategy that cannot price its
costs cannot say whether a trade is worth taking, so it must not take it.

Sources, read 2026-09-25 (docs/venues.md has the detail):
  Kalshi      general fee 0.07 x C x P x (1-P) for takers; maker fees on
              quadratic_with_maker_fees series at 0.25 of that, 0.5 on
              quadratic_with_combo_maker_fees; `flat` series use a separate
              table not read yet. Rounding (docs.kalshi.com, Fee Rounding):
              the fee is ceiled to $0.000001, then the balance change is
              floored to the cent for a non-direct member, the overpayment
              rebated later from a per-order accumulator. conservative=True
              charges that cent rounding and ignores the rebate.
  Polymarket  fee = C x rate x p x (1-p), takers only, makers never
              (docs.polymarket.com/trading/fees).
"""
from decimal import ROUND_CEILING, Decimal

KALSHI_TAKER = Decimal("0.07")
KALSHI_MAKER_SHARE = {
    "quadratic": Decimal("0"),
    "quadratic_with_maker_fees": Decimal("0.25"),
    "quadratic_with_combo_maker_fees": Decimal("0.5"),
}

# Taker rate by market category, from Polymarket's fee page (2026-09-25).
POLYMARKET_RATES = {
    "geopolitics": 0.0,
    "finance": 0.04, "politics": 0.04, "mentions": 0.04, "tech": 0.04,
    "sports": 0.05, "economics": 0.05, "culture": 0.05, "weather": 0.05,
    "other": 0.05,
    "crypto": 0.07,
}

ROLES = ("taker", "maker")
_MICRO = Decimal("0.000001")
_CENT = Decimal("0.01")


class UnknownFee(ValueError):
    """No fee rule we have read covers this. Refuse rather than guess."""


def kalshi_key(fee_type: str, multiplier=1) -> str:
    return f"kalshi:{fee_type}:{format(Decimal(str(multiplier)).normalize(), 'f')}"


def polymarket_key(category: str) -> str:
    cat = str(category or "").strip().lower()
    if cat not in POLYMARKET_RATES:
        raise UnknownFee(f"no Polymarket fee rate read for category {category!r}")
    return f"polymarket:{POLYMARKET_RATES[cat]:g}"


def _dec(x) -> Decimal:
    return Decimal(str(x))


def _rate(s: str) -> Decimal:
    """The multiplier or rate in a key. NaN, infinity or a negative number
    would make the fee nan, a crash or a rebate, so it is refused, not priced.
    Zero is a real rate: Polymarket charges nothing on geopolitics."""
    try:
        d = Decimal(s)
    except ArithmeticError:
        raise UnknownFee(f"fee number {s!r} is not a number") from None
    if not d.is_finite() or d < 0:
        raise UnknownFee(f"fee number {s!r} is not finite and non-negative")
    return d


def well_formed(model: str) -> bool:
    """The key has a shape this module knows. Says nothing about whether its
    fee can be computed: kalshi:flat:1 is well formed and still raises."""
    parts = str(model).split(":")
    try:
        if parts == ["book"]:
            return True
        if parts[0] == "kalshi" and len(parts) == 3 and parts[1]:
            _rate(parts[2])
            return True
        if parts[0] == "polymarket" and len(parts) == 2:
            _rate(parts[1])
            return True
    except UnknownFee:
        return False
    return False


def fee(model: str, price: float, contracts: float, role: str = "taker",
        conservative: bool = True) -> float:
    """Dollars of fee for one order of `contracts` at `price` (0 < price < 1)."""
    if role not in ROLES:
        raise ValueError(f"role must be taker or maker, got {role!r}")
    p, c = _dec(price), _dec(contracts)
    if not (Decimal(0) < p < Decimal(1)):
        raise ValueError(f"price {price} is not a probability strictly inside (0, 1)")
    if c <= 0:
        raise ValueError(f"contracts must be positive, got {contracts}")
    parts = str(model).split(":")

    if parts == ["book"]:
        return 0.0

    if parts[0] == "kalshi" and len(parts) == 3:
        _, fee_type, mult = parts
        if fee_type not in KALSHI_MAKER_SHARE:
            raise UnknownFee(f"Kalshi fee_type {fee_type!r} has not been read")
        share = Decimal(1) if role == "taker" else KALSHI_MAKER_SHARE[fee_type]
        model_fee = KALSHI_TAKER * _rate(mult) * share * c * p * (1 - p)
        trade_fee = model_fee.quantize(_MICRO, rounding=ROUND_CEILING)
        if not conservative:
            return float(trade_fee)
        cash = c * p + trade_fee
        return float(cash.quantize(_CENT, rounding=ROUND_CEILING) - c * p)

    if parts[0] == "polymarket" and len(parts) == 2:
        rate = _rate(parts[1])
        if role == "maker":
            return 0.0
        return float(c * rate * p * (1 - p))

    raise UnknownFee(f"unknown fee model {model!r}")


def cost_per_contract(model: str, price: float, contracts: float = 1,
                      role: str = "taker") -> float:
    """What one contract really costs: the price plus its share of the fee."""
    return price + fee(model, price, contracts, role) / contracts


def ev(p_win: float, model: str, price: float, contracts: float = 1,
       role: str = "taker") -> float:
    """Expected return per dollar staked, after fees. 0.02 means +2%.

    A contract pays 1 if it wins. For a book, price is 1/decimal odds and the
    fee is zero, so this is exactly p x decimal - 1.
    """
    return p_win / cost_per_contract(model, price, contracts, role) - 1
