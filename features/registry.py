"""Per-sport feature contracts. Each sport module exposes FEATURE_COLUMNS and
build_row(game) -> dict. Wire real data per sport; contracts keep the model
generic. Shared leakage-safe helper lives in features/build.py (rolling_asof).
"""
from features.sports import mlb_features, nfl_features, nba_features, nhl_features

REGISTRY = {
    "mlb": mlb_features,
    "nfl": nfl_features,
    "nba": nba_features,
    "nhl": nhl_features,
}
