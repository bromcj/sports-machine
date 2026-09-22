"""Bet engine: no-vig market prob, per-sport edge thresholds, fractional
Kelly, guardrails. Sport-agnostic math; sport-specific thresholds from config.

Universal rules (change deliberately, not on tilt):
  KELLY_FRACTION 0.25 | MAX_STAKE_PCT 0.03 | MAX_DAILY_PCT 0.10
No-bet guardrails by sport:
  MLB: opener / pitch-limited SP / unconfirmed SP
  NFL: QB1 unconfirmed
  NBA: star availability unresolved at lock
  NHL: starting goalie unconfirmed
"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
from config import SPORTS
from model.validation import is_cleared, explain

KELLY_FRACTION = 0.25
MAX_STAKE_PCT = 0.03
MAX_DAILY_PCT = 0.10

GUARDRAIL_FLAGS = {
    "mlb": ("opener_flag", "pitch_limit_flag", "sp_unconfirmed"),
    "nfl": ("qb_unconfirmed",),
    "nba": ("stars_unresolved",),
    "nhl": ("goalie_unconfirmed",),
}


def american_to_prob(ml: int) -> float:
    return 100 / (ml + 100) if ml > 0 else -ml / (-ml + 100)


def american_to_decimal(ml: int) -> float:
    return 1 + ml / 100 if ml > 0 else 1 + 100 / -ml


def novig_probs(away_ml: int, home_ml: int) -> tuple[float, float]:
    pa, ph = american_to_prob(away_ml), american_to_prob(home_ml)
    total = pa + ph
    return pa / total, ph / total


def kelly_stake(model_prob: float, ml: int, bankroll: float) -> float:
    b = american_to_decimal(ml) - 1
    f = (model_prob * b - (1 - model_prob)) / b
    if f <= 0:
        return 0.0
    return round(bankroll * min(f * KELLY_FRACTION, MAX_STAKE_PCT), 2)


def evaluate(sport: str, model_home_prob: float, away_ml: int, home_ml: int,
             bankroll: float, flags: dict | None = None,
             allow_unvalidated: bool = False) -> dict:
    """Decide whether to bet, and how much.

    A sport whose walk-forward has not beaten the real market is refused before
    any edge math runs. min_edge measures DISAGREEMENT with the price, which is
    only worth money when the model forecasts better than the price does. The
    NFL v1 model disagreed on 74% of games, claimed +12.5%, and returned -9.3%.

    allow_unvalidated=True is for backtests that must score an uncleared model
    on purpose. Never set it on a path that can stake real money.
    """
    flags = flags or {}
    if not (allow_unvalidated or is_cleared(sport)):
        return {"sport": sport, "bet": False,
                "reason": "not validated: " + explain(sport)}
    min_edge = SPORTS[sport]["min_edge"]
    blocked = [k for k in GUARDRAIL_FLAGS.get(sport, ()) if flags.get(k)]
    novig_away, novig_home = novig_probs(away_ml, home_ml)
    edge_home = model_home_prob - novig_home
    edge_away = (1 - model_home_prob) - novig_away

    side, edge, ml, prob, novig = None, 0.0, None, None, None
    if edge_home >= min_edge and edge_home >= edge_away:
        side, edge, ml, prob, novig = "home", edge_home, home_ml, model_home_prob, novig_home
    elif edge_away >= min_edge:
        side, edge, ml, prob, novig = "away", edge_away, away_ml, 1 - model_home_prob, novig_away

    if side is None or blocked:
        return {"sport": sport, "bet": False,
                "reason": ("guardrail:" + ",".join(blocked)) if blocked
                else f"edge below {min_edge:.1%}",
                "edge_home": edge_home, "edge_away": edge_away}
    return {"sport": sport, "bet": True, "side": side, "line": ml,
            "edge": round(edge, 4), "model_prob": round(prob, 4),
            "novig_market_prob": round(novig, 4),
            "stake": kelly_stake(prob, ml, bankroll),
            "kelly_fraction": KELLY_FRACTION}


def clv_pct(line_taken: int, closing_line: int) -> float:
    return round((american_to_decimal(line_taken) /
                  american_to_decimal(closing_line) - 1) * 100, 2)


if __name__ == "__main__":
    from model.validation import report
    print(report())
    print()
    # Every sport is refused until its walk-forward beats a real market.
    for sp in ("mlb", "nfl", "nhl"):
        print(sp, "->", evaluate(sp, 0.62, +130, -150, 1000)["reason"])
    print()
    # The edge math itself, with the guard deliberately bypassed.
    print("bypassed:", evaluate("mlb", 0.62, +130, -150, 1000, allow_unvalidated=True))
    print("bypassed:", evaluate("nhl", 0.62, +130, -150, 1000,
                                {"goalie_unconfirmed": True}, allow_unvalidated=True))
    print("CLV -104 -> close -120:", clv_pct(-104, -120), "%")
