"""NFL features. Data sources: nfl_data_py (play-by-play, free), ESPN.
EPA/play is the modern workhorse stat; QB status dominates lines."""
FEATURE_COLUMNS = [
    "home_off_epa_5g", "away_off_epa_5g",         # rolling offensive EPA/play
    "home_def_epa_5g", "away_def_epa_5g",
    "home_qb_starter_flag", "away_qb_starter_flag",  # QB1 confirmed vs backup
    "home_rest_days", "away_rest_days",           # short week / bye detection
    "home_travel_tz", "dome_flag", "wind_over15_flag",
    "home_eliminated", "away_eliminated",          # late-season motivation
]

def build_row(game) -> dict:
    return {c: None for c in FEATURE_COLUMNS}  # TODO: wire nfl_data_py
