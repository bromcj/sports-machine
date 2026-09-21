"""MLB features — real implementation, 100% Statcast-derived (v1).

No FanGraphs dependency: bullpen quality/fatigue, team offense, and starter
form all come from pitch-level Statcast data in data/statcast/.
Leakage guard: every rolling stat is shifted so a game never sees itself.
"""
import pandas as pd
import numpy as np
from pathlib import Path

STATCAST_DIR = Path(__file__).parent.parent.parent / "data" / "statcast"

FEATURE_COLUMNS = [
    "home_pen_kbb_30d", "away_pen_kbb_30d",        # bullpen K-BB% (quality)
    "home_pen_pitches_3d", "away_pen_pitches_3d",  # bullpen fatigue
    "home_off_woba_30d", "away_off_woba_30d",      # team offense aggregate
    "home_sp_kbb_5s", "away_sp_kbb_5s",            # starter K-BB% last 5 starts
    "home_sp_elite", "away_sp_elite",              # top-decile SP flag
    "home_sp_bottom", "away_sp_bottom",            # bottom-decile SP flag
    "home_rest_days", "away_rest_days",
    "park_factor",
]

PARK_FACTORS = {  # runs, 1.00 = neutral (static v1; refresh yearly)
    "COL": 1.12, "CIN": 1.06, "BOS": 1.05, "KC": 1.04, "AZ": 1.03,
    "TEX": 1.02, "PHI": 1.02, "BAL": 1.01, "MIN": 1.01, "ATL": 1.01,
    "TOR": 1.00, "CHC": 1.00, "WSH": 1.00, "LAA": 1.00, "PIT": 0.99,
    "MIL": 0.99, "STL": 0.99, "NYY": 0.99, "HOU": 0.99,
    "DET": 0.98, "CWS": 0.98, "SD": 0.97, "TB": 0.97, "LAD": 0.97,
    "NYM": 0.96, "CLE": 0.96, "OAK": 0.96, "ATH": 0.96, "SF": 0.95,
    "SEA": 0.94, "MIA": 0.94,
}

TEAM_ABBR = {
    "Arizona Diamondbacks": "AZ", "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL", "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC", "Chicago White Sox": "CWS",
    "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL", "Detroit Tigers": "DET",
    "Houston Astros": "HOU", "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA", "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN", "New York Mets": "NYM",
    "New York Yankees": "NYY", "Oakland Athletics": "OAK",
    "Athletics": "ATH", "Philadelphia Phillies": "PHI",
    "Pittsburgh Pirates": "PIT", "San Diego Padres": "SD",
    "San Francisco Giants": "SF", "Seattle Mariners": "SEA",
    "St. Louis Cardinals": "STL", "Tampa Bay Rays": "TB",
    "Texas Rangers": "TEX", "Toronto Blue Jays": "TOR",
    "Washington Nationals": "WSH",
}


def load_statcast(years=None) -> pd.DataFrame:
    files = sorted(STATCAST_DIR.glob("*.parquet"))
    if years:
        files = [f for f in files if int(f.stem) in years]
    if not files:
        raise FileNotFoundError(
            f"No Statcast parquet in {STATCAST_DIR}. Run: python backfill.py")
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["game_date"] = pd.to_datetime(df["game_date"])
    df["pitch_team"] = np.where(df["inning_topbot"] == "Top",
                                df["home_team"], df["away_team"])
    df["bat_team"] = np.where(df["inning_topbot"] == "Top",
                              df["away_team"], df["home_team"])
    return df


def pitcher_game_lines(sc: pd.DataFrame) -> pd.DataFrame:
    """One row per pitcher-game: role, pitches, PA, K, BB."""
    pa = sc[sc["events"].notna()]
    lines = (pa.groupby(["game_pk", "game_date", "pitch_team", "pitcher"])
               .agg(pa=("events", "size"),
                    k=("events", lambda s: (s == "strikeout").sum()),
                    bb=("events", lambda s: s.isin(["walk", "hit_by_pitch"]).sum()))
               .reset_index())
    pitches = (sc.groupby(["game_pk", "pitcher"]).size()
                 .rename("pitches").reset_index())
    lines = lines.merge(pitches, on=["game_pk", "pitcher"])
    first_ab = (sc.groupby(["game_pk", "pitch_team", "pitcher"])["at_bat_number"]
                  .min().rename("first_ab").reset_index())
    lines = lines.merge(first_ab, on=["game_pk", "pitch_team", "pitcher"])
    starter_ab = lines.groupby(["game_pk", "pitch_team"])["first_ab"].transform("min")
    lines["is_starter"] = lines["first_ab"] == starter_ab
    return lines


def bullpen_table(lines: pd.DataFrame) -> pd.DataFrame:
    """(team, date) -> pen_kbb_30d, pen_pitches_3d, both as-of (shifted)."""
    pen = (lines[~lines["is_starter"]]
           .groupby(["pitch_team", "game_date"])
           .agg(pa=("pa", "sum"), k=("k", "sum"), bb=("bb", "sum"),
                pitches=("pitches", "sum"))
           .reset_index().sort_values("game_date"))
    out = []
    for team, g in pen.groupby("pitch_team"):
        g = g.set_index("game_date").sort_index()
        g["pen_kbb_30d"] = ((g["k"] - g["bb"]).rolling("30D").sum()
                            / g["pa"].rolling("30D").sum()).shift(1)
        g["pen_pitches_3d"] = g["pitches"].rolling("3D").sum().shift(1).fillna(0)
        g = g.reset_index()
        g["team"] = team
        out.append(g[["team", "game_date", "pen_kbb_30d", "pen_pitches_3d"]])
    return pd.concat(out, ignore_index=True).sort_values("game_date")


def offense_table(sc: pd.DataFrame) -> pd.DataFrame:
    """(team, date) -> off_woba_30d, as-of (shifted)."""
    pa = sc[sc["woba_denom"].notna() & (sc["woba_denom"] > 0)]
    off = (pa.groupby(["bat_team", "game_date"])
             .agg(wv=("woba_value", "sum"), wd=("woba_denom", "sum"))
             .reset_index().sort_values("game_date"))
    out = []
    for team, g in off.groupby("bat_team"):
        g = g.set_index("game_date").sort_index()
        g["off_woba_30d"] = (g["wv"].rolling("30D").sum()
                             / g["wd"].rolling("30D").sum()).shift(1)
        g = g.reset_index()
        g["team"] = team
        out.append(g[["team", "game_date", "off_woba_30d"]])
    return pd.concat(out, ignore_index=True).sort_values("game_date")


def starter_table(lines: pd.DataFrame) -> pd.DataFrame:
    """One row per (game_pk, team): the starter's shifted 5-start form + flags."""
    sp = lines[lines["is_starter"]].sort_values("game_date").copy()
    out = []
    for _, g in sp.groupby("pitcher"):
        g = g.sort_values("game_date").copy()
        g["sp_kbb_5s"] = ((g["k"] - g["bb"]).rolling(5, min_periods=2).sum()
                          / g["pa"].rolling(5, min_periods=2).sum()).shift(1)
        out.append(g)
    sp = pd.concat(out, ignore_index=True).sort_values("game_date")
    sp["q_hi"] = sp["sp_kbb_5s"].expanding(min_periods=50).quantile(0.90)
    sp["q_lo"] = sp["sp_kbb_5s"].expanding(min_periods=50).quantile(0.10)
    sp["sp_elite"] = (sp["sp_kbb_5s"] >= sp["q_hi"]).astype(float)
    sp["sp_bottom"] = (sp["sp_kbb_5s"] <= sp["q_lo"]).astype(float)
    return sp[["game_pk", "pitch_team", "sp_kbb_5s", "sp_elite", "sp_bottom"]]


def build_row(game) -> dict:
    return {c: None for c in FEATURE_COLUMNS}  # live path filled in v2
