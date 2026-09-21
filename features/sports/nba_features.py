"""NBA features. Data: nba_api (free, official stats). Rest/injury dominates —
back-to-backs and star availability move lines more than season-long quality."""
FEATURE_COLUMNS = [
    "home_net_rtg_10g", "away_net_rtg_10g",       # rolling net rating
    "home_b2b_flag", "away_b2b_flag",             # back-to-back
    "home_3in4_flag", "away_3in4_flag",
    "home_stars_out_count", "away_stars_out_count",  # injury report weight
    "home_travel_miles_3g", "away_travel_miles_3g",
    "home_tank_flag", "away_tank_flag",           # late-season lottery seeding
]

def build_row(game) -> dict:
    return {c: None for c in FEATURE_COLUMNS}  # TODO: wire nba_api + injury feed
