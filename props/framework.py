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
from dataclasses import dataclass

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
