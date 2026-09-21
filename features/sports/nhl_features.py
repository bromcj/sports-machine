"""NHL features. Data: NHL API (free), MoneyPuck CSVs (free downloads).
Goalies are the SP-equivalent: confirm the starter before betting."""
FEATURE_COLUMNS = [
    "home_xgf_pct_10g", "away_xgf_pct_10g",       # 5v5 expected-goals share
    "home_goalie_gsax_season", "away_goalie_gsax_season",  # goals saved above expected
    "home_goalie_confirmed", "away_goalie_confirmed",
    "home_b2b_flag", "away_b2b_flag",
    "home_pp_pct_20g", "away_pk_pct_20g",
    "away_pp_pct_20g", "home_pk_pct_20g",
]

def build_row(game) -> dict:
    return {c: None for c in FEATURE_COLUMNS}  # TODO: wire NHL API + MoneyPuck
