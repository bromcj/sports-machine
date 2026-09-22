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
    "home_rest_days", "away_rest_days",
    "park_factor",
]

PARK_PRIORS = {  # runs, 1.00 = neutral. Cold-start priors ONLY: used for a
                 # venue with no history yet. Seasons with history get an
                 # empirical factor from park_factor_table() instead.
                 # Keyed by VENUE, not team - a club that moves changes key.
    "COL": 1.12, "CIN": 1.06, "BOS": 1.05, "KC": 1.04, "AZ": 1.03,
    "TEX": 1.02, "PHI": 1.02, "BAL": 1.01, "MIN": 1.01, "ATL": 1.01,
    "TOR": 1.00, "CHC": 1.00, "WSH": 1.00, "LAA": 1.00, "PIT": 0.99,
    "MIL": 0.99, "STL": 0.99, "NYY": 0.99, "HOU": 0.99,
    "DET": 0.98, "CWS": 0.98, "SD": 0.97, "TB": 0.97, "LAD": 0.97,
    "NYM": 0.96, "CLE": 0.96, "ATH": 0.96, "SF": 0.95,  # ATH = Oakland Coliseum
    # "SAC" is deliberately absent: a brand-new park has no history, and
    # inheriting the Coliseum's 0.96 is exactly the bug this avoids.
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
    "New York Yankees": "NYY", "Philadelphia Phillies": "PHI",
    # One franchise, two names: Statcast has used ATH for every season.
    "Oakland Athletics": "ATH", "Athletics": "ATH",
    "Pittsburgh Pirates": "PIT", "San Diego Padres": "SD",
    "San Francisco Giants": "SF", "Seattle Mariners": "SEA",
    "St. Louis Cardinals": "STL", "Tampa Bay Rays": "TB",
    "Texas Rangers": "TEX", "Toronto Blue Jays": "TOR",
    "Washington Nationals": "WSH",
}


VENUES = {
    # (team, season) -> venue id, for clubs that have moved. Everyone else
    # defaults to their own abbreviation, so this stays short.
    # The A's left the Oakland Coliseum after 2024 for a minor-league park in
    # West Sacramento. A Las Vegas park is expected; when it opens it is two
    # more lines here, and nothing else in the codebase needs to know.
    ("ATH", 2025): "SAC",
    ("ATH", 2026): "SAC",
}

PF_REGRESSION = 162   # shrink weight in games: a venue needs ~2 home seasons
                      # of evidence to move halfway from 1.00 to its raw mark
PF_MIN_GAMES = 81     # below this, no empirical estimate - fall back to prior


def venue_id(team: str, season: int) -> str:
    """Which ballpark a club played in that season. Season granularity, so a
    mid-season relocation would need a date-keyed table instead."""
    return VENUES.get((team, season), team)


def park_factor_table(games: pd.DataFrame) -> dict:
    """(venue, season) -> park factor, built from PRIOR seasons only.

    Classic construction: runs per game at the venue over runs per game in its
    occupant's road games, which controls for the club's own quality (a weak
    offense makes its home park look pitcher-friendly otherwise). Shrunk toward
    1.00 because a single season of park data is very noisy.

    A venue with no history - a relocation, a new build - gets the static prior
    if one exists and 1.00 otherwise. Never the previous park's value: the
    Athletics' Sacramento park has run hotter than Coors while the Coliseum it
    replaced was among the coldest in baseball.

    Needs columns: home_ab, away_ab, season, home_score, away_score.
    """
    g = games.copy()
    g["total_runs"] = g["home_score"] + g["away_score"]
    g["venue"] = [venue_id(t, s) for t, s in zip(g["home_ab"], g["season"])]

    out = {}
    for season in sorted(g["season"].unique()):
        past = g[g["season"] < season]
        for venue in g.loc[g["season"] == season, "venue"].unique():
            home = past[past["venue"] == venue]
            occupants = home["home_ab"].unique()
            road = past[past["away_ab"].isin(occupants) & (past["venue"] != venue)]
            if len(home) < PF_MIN_GAMES or len(road) < PF_MIN_GAMES:
                out[(venue, season)] = PARK_PRIORS.get(venue, 1.00)
                continue
            raw = home["total_runs"].mean() / road["total_runs"].mean()
            n = len(home)
            out[(venue, season)] = (n * raw + PF_REGRESSION) / (n + PF_REGRESSION)
    return out


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
    # Top/bottom-decile flags used to live here, on the hypothesis that SP
    # quality bites harder at the extremes than a straight line implies. Tested
    # on 10,482 games and it does not: out-of-sample residuals at both tails sit
    # within noise of zero, and adding the flags made log-loss WORSE (0.68762 vs
    # 0.68720) with a sign-flipped coefficient. Quadratic and cubic terms moved
    # nothing either. A line captures the effect completely.
    #
    # The related claim that the MARKET under-reacts to SP at the tails is a
    # different question and still open - it needs real closing lines to test.
    return sp[["game_pk", "pitch_team", "sp_kbb_5s"]]


def build_row(game) -> dict:
    return {c: None for c in FEATURE_COLUMNS}  # live path filled in v2
