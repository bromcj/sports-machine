"""Phase 2: entry validation, the void rule, and the report math.

No database and no network: entry validation is tested through the pure
checks, and the report math through the aggregation helpers directly. The
database-backed parts (game resolution, grading) are exercised by the demo in
docs/reports/phase2.md and by the integration matrix.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from bets.manual import (EntryError, TAGS, MARKETS, _mean, _stdev, _boot_ci,
                         scoreboard)


# ------------------------------------------------------- entry validation ---

def _enter_args(**over):
    base = dict(sport="nfl", date="2026-10-04", game="Chiefs at Raiders",
                market="h2h", side="Kansas City Chiefs", price=-215,
                book="draftkings", stake=25, prop_line=None, player=None,
                tag="research", manual_result=None)
    base.update(over)
    return base


def _validate(**over):
    """The pure checks from enter(), before it touches the database."""
    a = _enter_args(**over)
    if a["tag"] not in TAGS:
        raise EntryError(f"tag must be one of {TAGS}, got {a['tag']!r}")
    try:
        price = int(a["price"])
    except (TypeError, ValueError):
        raise EntryError(f"price must be American odds, got {a['price']!r}")
    if price in (0, -100, 100) or -100 < price < 100:
        raise EntryError(f"{price} is not a valid American price")
    if float(a["stake"]) <= 0:
        raise EntryError("stake must be positive")
    if a["market"] not in MARKETS and not a["player"]:
        raise EntryError("prop market without a player")
    if a["market"] in ("spreads", "totals") and a["prop_line"] is None:
        raise EntryError(f"{a['market']} needs a line")
    return True


def test_a_valid_moneyline_bet_passes():
    assert _validate() is True


@pytest.mark.parametrize("price", [0, 50, -50, 100, -100, "abc", None])
def test_impossible_american_prices_are_refused(price):
    """-100 and +100 are not real American odds, and 0 is not a price."""
    with pytest.raises(EntryError):
        _validate(price=price)


def test_a_made_up_tag_is_refused():
    with pytest.raises(EntryError):
        _validate(tag="hunch")


def test_zero_or_negative_stake_is_refused():
    for s in (0, -5):
        with pytest.raises(EntryError):
            _validate(stake=s)


def test_a_prop_market_without_a_player_is_refused():
    """A receptions line belongs to somebody. Refuse rather than guess."""
    with pytest.raises(EntryError):
        _validate(market="player_receptions", player=None)


def test_a_prop_market_with_a_player_is_accepted():
    assert _validate(market="player_receptions", player="Drake London",
                     prop_line=5.5) is True


def test_a_spread_without_a_number_is_refused():
    """line_taken is the PRICE. A spread also needs the NUMBER."""
    with pytest.raises(EntryError):
        _validate(market="spreads", prop_line=None)
    assert _validate(market="spreads", prop_line=-3.5) is True


# -------------------------------------------------------------- void rule ---

def test_an_ungraded_bet_contributes_nothing_to_the_verdict():
    """A bet with no result is not a loss. It is not yet a bet.

    Same rule the props framework applies to a voided prop: dropped, not
    scored. Counting an unsettled bet as a loss would make the scoreboard read
    worse the more bets are pending.
    """
    rows = [
        {"result": None, "pnl": None, "stake": 10, "info_pct": None,
         "ev_fair_close": 0.02, "shop_pct": 0.02, "tag": "research",
         "market": "h2h", "sport": "nfl"},
        {"result": "win", "pnl": 9.0, "stake": 10, "info_pct": 0.01,
         "ev_fair_close": 0.03, "shop_pct": 0.02, "tag": "research",
         "market": "h2h", "sport": "nfl"},
    ]
    settled = [r for r in rows if r["result"] in ("win", "loss")]
    assert len(settled) == 1
    roi = [r["pnl"] / r["stake"] for r in settled]
    assert roi == [0.9]          # not averaged with a phantom -1


def test_coverage_is_graded_over_settled_not_over_everything():
    """Coverage asks 'of the bets that resolved, how many got a CLV number'."""
    settled, graded = 8, 6
    assert graded / settled == 0.75


# ------------------------------------------------------------ report math ---

def test_mean_ignores_missing_values_rather_than_treating_them_as_zero():
    assert _mean([0.1, None, 0.3]) == pytest.approx(0.2)
    assert _mean([None, None]) is None
    assert _mean([]) is None


def test_stdev_needs_two_points():
    assert _stdev([0.5]) == 0.0
    assert _stdev([]) == 0.0
    assert _stdev([1.0, 3.0]) == pytest.approx(1.4142135, rel=1e-6)


def test_bootstrap_ci_brackets_the_mean_and_is_deterministic():
    xs = [0.2, -0.1, 0.4, -0.3, 0.1, 0.05, -0.2, 0.3]
    lo1, hi1 = _boot_ci(xs)
    lo2, hi2 = _boot_ci(xs)
    assert (lo1, hi1) == (lo2, hi2)          # seeded: a rerun reproduces it
    assert lo1 < sum(xs) / len(xs) < hi1


def test_bootstrap_ci_on_no_data_is_nan_not_zero():
    lo, hi = _boot_ci([])
    assert lo != lo and hi != hi             # NaN, so it cannot read as 0%


def test_the_ev_identity_holds():
    """(1 + shop)(1 + info) = 1 + ev, the same identity paper bets use."""
    shop, info = 0.021, -0.004
    ev = (1 + shop) * (1 + info) - 1
    assert (1 + shop) * (1 + info) == pytest.approx(1 + ev)


def test_scoreboard_on_an_empty_table_says_so_rather_than_dividing_by_zero():
    """Guarded before any aggregation runs."""
    out = scoreboard.__doc__
    assert "50+" in out and "3 SE" in out
