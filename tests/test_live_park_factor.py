"""features/build._row must key the park factor the way training does.

Training keys park_factor_table() on the Stats API venue id ('19'); the live
path looked it up by team code ('COL'), never matched, and served the static
prior on every live row - 1.12 at Coors where training used 1.2245.
"""
import pandas as pd

from features.build import _row
from features.sports.mlb_features import PARK_PRIORS, TEAM_ABBR

HOME, AWAY = "Colorado Rockies", "Arizona Diamondbacks"
H, A = TEAM_ABBR[HOME], TEAM_ABBR[AWAY]
ASOF = pd.Timestamp("2026-09-24")


def _inputs(venue_id):
    g = {"home": HOME, "away": AWAY, "home_starter_id": 1, "away_starter_id": 2,
         "home_starter": "h", "away_starter": "a", "venue_id": venue_id}
    pen = {H: (0.1, 50.0), A: (0.1, 50.0)}
    off = {H: 0.32, A: 0.31}
    sform = {1: 0.15, 2: 0.12}
    rest = {H: 1.0, A: 1.0}
    pf = {("19", 2026): 1.2245}
    return g, pen, off, sform, rest, pf, ASOF


def test_the_live_row_uses_the_venue_keyed_factor():
    vec, why = _row(*_inputs("19"))
    assert why is None
    assert vec["park_factor"] == 1.2245


def test_without_a_venue_id_it_falls_back_to_the_team_prior():
    vec, _ = _row(*_inputs(None))
    assert vec["park_factor"] == PARK_PRIORS[H]
