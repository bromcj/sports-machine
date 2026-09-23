"""Per-sport feature contracts. Each sport module exposes FEATURE_COLUMNS and
build_row(game) -> dict. Wire real data per sport; contracts keep the model
generic. Shared leakage-safe helper lives in features/build.py (rolling_asof).

Nothing imports this yet - features/build.py is MLB-only and calls the mlb
module directly. It is kept as the contract a second wired sport would follow.
"""
from features.sports import (mlb_features, nfl_features_v1, nba_features,
                             nhl_features)

REGISTRY = {
    "mlb": mlb_features,
    "nfl": nfl_features_v1,
    "nba": nba_features,
    "nhl": nhl_features,
}
