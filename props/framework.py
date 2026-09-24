"""The one way every prop gets modelled. Sport-agnostic, no credits.

docs/experiments.md B1. Every prop is OPPORTUNITY x RATE:

    strikeouts       = batters faced      x  K% per batter
    receptions       = targets            x  catch rate
    receiving yards  = targets            x  yards per target
    rushing yards    = carries            x  yards per carry

The split is not cosmetic. The two halves have completely different
statistical characters and completely different edges available:

  OPPORTUNITY is the information half. How many batters a starter faces, or
  how many targets a receiver sees, is driven by role, health, and game script
  - and game script is something the MARKET HANDS US FOR FREE in the spread and
  the total, which are legal pre-game inputs. A team that is going to trail by
  ten throws more. This is where a small operation can be ahead of a book,
  because books price props off season-long rates and are slow to re-cut them
  when a role changes.

  RATE is the skill half, and it is where everyone including us is worst. Catch
  rate and K% per batter are noisy and regress hard. The honest prior is that
  our rate estimate is no better than the market's.

So the framework keeps them separate, and B3/B4 will report which half any edge
came from. An edge that is entirely rate is probably noise; an edge that is
entirely opportunity is the thing we came for.

SHRINKAGE. Both halves are shrunk toward a prior by their own sample size, the
same way research/pitching.py projects a starter. A receiver with 9 targets is
not a 9-target receiver, and a model that thinks he is will chase every small
sample in the league.

WHAT THIS MODULE DOES NOT DO. It does not know about any sport. It takes
numbers that a sport-specific builder produced and turns them into a
probability, a comparison against a market price, and a graded result. Each new
prop is a PropSpec plus a builder - not a new codebase.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from props.distributions import count_dist, yards_dist, over_push_under, \
    prob_over_excluding_push


@dataclass(frozen=True)
class PropSpec:
    """One prop type. The market key is the only thing the odds feed sees."""
    key: str                      # e.g. "pitcher_strikeouts"
    sport: str                    # "mlb" | "nfl"
    kind: str                     # "count" | "yards"
    opportunity: str              # column: projected opportunities
    rate: str                     # column: projected rate per opportunity
    # Spread of the outcome around its mean. For counts this is Var/mean; for
    # yards it is the coefficient of variation. Both are properties of the
    # PROP, not of the player, and both are estimated from history once rather
    # than fitted per row - fitting dispersion per player on small samples is
    # how a model ends up certain about a rookie.
    dispersion: float = 1.25
    # Team-level correlation guard: at most one bet per team per market until
    # correlation is modelled. Two receivers on the same team are not two
    # independent bets - they are competing for the same targets.
    one_per_team: bool = True
    notes: str = ""


# The four the brief names, with market keys confirmed against the Odds API
# docs. Dispersions are placeholders until B3/B4 measure them from real
# outcomes; they are deliberately WIDE, because an overconfident prop model
# bets more, not less.
SPECS = {
    "pitcher_strikeouts": PropSpec(
        "pitcher_strikeouts", "mlb", "count", "proj_bf", "proj_k_rate",
        dispersion=1.30,
        notes="batters faced x K% per batter, vs the lineup's K% against that "
              "handedness, park-adjusted"),
    "player_receptions": PropSpec(
        "player_receptions", "nfl", "count", "proj_targets", "proj_catch_rate",
        dispersion=1.35,
        notes="targets x catch rate; vacated target share is the explicit "
              "feature"),
    "player_reception_yds": PropSpec(
        "player_reception_yds", "nfl", "yards", "proj_targets",
        "proj_yds_per_target", dispersion=0.85),
    "player_rush_yds": PropSpec(
        "player_rush_yds", "nfl", "yards", "proj_carries",
        "proj_yds_per_carry", dispersion=0.80),
}


def shrink(observed_events: float, observed_rate: float, prior_rate: float,
           regress_events: float) -> float:
    """Shrink a rate toward a prior by how many events it was measured over.

    regress_events is the number of events at which the estimate sits halfway
    between what was observed and the prior. For a catch rate that is ~50
    targets; for K% per batter ~300 batters. These are properties of how fast
    the statistic stabilises and they are NOT tuned against outcomes here -
    tuning them on the test data is the thing this whole project exists to
    avoid.
    """
    observed_events = max(float(observed_events), 0.0)
    w = observed_events / (observed_events + float(regress_events))
    return w * float(observed_rate) + (1.0 - w) * float(prior_rate)


def project(spec: PropSpec, rows: pd.DataFrame) -> np.ndarray:
    """Expected value of the prop: opportunity x rate."""
    for c in (spec.opportunity, spec.rate):
        if c not in rows.columns:
            raise KeyError(f"{spec.key} needs column '{c}'")
    return rows[spec.opportunity].values * rows[spec.rate].values


def distribution_for(spec: PropSpec, mean: float):
    if spec.kind == "count":
        return count_dist(mean, spec.dispersion)
    return yards_dist(mean, spec.dispersion)


def price(spec: PropSpec, rows: pd.DataFrame,
          line_col: str = "line") -> pd.DataFrame:
    """P(over), P(push), P(under) and the push-conditional P(over) per row."""
    means = project(spec, rows)
    out = []
    for mean, line in zip(means, rows[line_col].values):
        d, discrete = distribution_for(spec, mean)
        o, p, u = over_push_under(d, line, discrete)
        out.append((mean, o, p, u, prob_over_excluding_push(d, line, discrete)))
    return pd.DataFrame(
        out, index=rows.index,
        columns=["projection", "p_over", "p_push", "p_under", "p_over_live"])


# ------------------------------------------------------------- the rules ---

def drop_voids(rows: pd.DataFrame, played_col: str = "played") -> pd.DataFrame:
    """Props where the listed player did not appear are DROPPED, not scored.

    The book voids them and returns the stake, so they are neither a win nor a
    loss and they never entered the sample at all. Scoring them as losses would
    punish the model for a bet that was never made; scoring them as wins would
    be worse. Either way the sample would no longer be the set of bets that
    could actually have been placed.

    This matters more than it sounds for pitcher props specifically: a scratched
    starter is not random, it correlates with the same injury news the market
    was already reacting to.
    """
    if played_col not in rows.columns:
        raise KeyError(
            f"cannot apply the void rule without a '{played_col}' column - "
            f"refusing to guess that everyone played")
    return rows[rows[played_col].astype(bool)].copy()


def one_bet_per_team(rows: pd.DataFrame, spec: PropSpec,
                     edge_col: str = "edge", team_col: str = "team",
                     game_col: str = "game_id") -> pd.DataFrame:
    """Keep only the best edge per (game, team) for this market.

    Two receivers on the same team are competing for the same targets: if the
    quarterback has a bad day they both miss, so the two bets lose together.
    Treating them as independent overstates the sample size and understates the
    risk, which is the same mistake in two different places. Until that
    correlation is modelled explicitly the framework simply refuses to take
    both.
    """
    if not spec.one_per_team:
        return rows.copy()
    for c in (edge_col, team_col, game_col):
        if c not in rows.columns:
            raise KeyError(f"one_bet_per_team needs '{c}'")
    idx = (rows.assign(_abs=rows[edge_col].abs())
               .sort_values("_abs", ascending=False)
               .groupby([game_col, team_col], sort=False).head(1).index)
    return rows.loc[sorted(idx)].copy()


def devig_two_way(over_price: float, under_price: float) -> tuple:
    """De-vig a two-way prop market. (p_over, p_under, overround).

    Same proportional method as bets/engine.novig_probs, deliberately: props
    and sides have to be de-vigged identically or a comparison between them is
    a comparison of two methods.
    """
    from bets.engine import american_to_prob
    a, b = american_to_prob(over_price), american_to_prob(under_price)
    total = a + b
    return a / total, b / total, total - 1.0


def edge_vs_market(model_p_over: np.ndarray, market_p_over: np.ndarray
                   ) -> np.ndarray:
    """Model probability minus the de-vigged market probability, on the over.

    Disagreement, NOT edge - the same distinction CLAUDE.md records for
    min_edge on the moneyline side. It is only worth money if the model is
    better calibrated than the price, which is what B3/B4 exist to test and
    which has never yet been true in this project.
    """
    return np.asarray(model_p_over, float) - np.asarray(market_p_over, float)
