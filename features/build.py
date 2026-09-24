"""Build the feature vector for games that have NOT been played yet.

features/build_training.py answers "what did the numbers look like before each
past game." This answers the same question for tonight's slate, using the same
functions, so the model sees inputs shaped exactly like the ones it learned on.

Two things make the live path different from the training path, and both are
places to be careful rather than clever:

  1. THE STARTER IS A PROBABLE, NOT A FACT. Training reads who actually threw
     the first pitch, from Statcast. Here the best available answer is the
     probable pitcher announced by the MLB Stats API, which can change. Games
     without both probables get no feature row at all - a guessed starter is
     worse than no pick.

  2. STATCAST LAGS. The rolling windows can only see games already downloaded.
     If data/statcast is days behind, a "30-day" window silently becomes a
     30-days-ending-last-Tuesday window. Staleness is reported loudly and
     recorded in the feature payload rather than being papered over.

Rows are written to the `features` table as JSON, keyed by game_id.
"""
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from db import connect
from features.sports.mlb_features import (
    PARK_PRIORS, TEAM_ABBR, load_statcast,
    park_factor_table, pitcher_game_lines, venue_id)

STALE_WARN_DAYS = 2


def _asof(df: pd.DataFrame, date_col: str, asof: pd.Timestamp) -> pd.DataFrame:
    """Only rows strictly before asof. This is the leakage guard for the live
    path: the same shift(1) the training tables get, expressed as a filter."""
    return df[df[date_col] < asof]


def bullpen_asof(lines: pd.DataFrame, asof: pd.Timestamp) -> dict:
    """team -> (pen_kbb_30d, pen_pitches_3d) using only games before asof."""
    pen = _asof(lines[~lines["is_starter"]], "game_date", asof)
    out = {}
    w30 = pen[pen["game_date"] >= asof - pd.Timedelta(days=30)]
    w3 = pen[pen["game_date"] >= asof - pd.Timedelta(days=3)]
    for team, g in w30.groupby("pitch_team"):
        pa = g["pa"].sum()
        out[team] = [float((g["k"].sum() - g["bb"].sum()) / pa) if pa else None, 0.0]
    for team, g in w3.groupby("pitch_team"):
        if team in out:
            out[team][1] = float(g["pitches"].sum())
    return out


def offense_asof(sc: pd.DataFrame, asof: pd.Timestamp) -> dict:
    """team -> off_woba_30d using only pitches before asof."""
    pa = sc[sc["woba_denom"].notna() & (sc["woba_denom"] > 0)]
    pa = _asof(pa, "game_date", asof)
    pa = pa[pa["game_date"] >= asof - pd.Timedelta(days=30)]
    out = {}
    for team, g in pa.groupby("bat_team"):
        denom = g["woba_denom"].sum()
        out[team] = float(g["woba_value"].sum() / denom) if denom else None
    return out


def starters_asof(lines: pd.DataFrame, asof: pd.Timestamp) -> dict:
    """pitcher_id -> sp_kbb_5s over their last 5 starts."""
    sp = _asof(lines[lines["is_starter"]], "game_date", asof)
    form = {}
    for pid, g in sp.groupby("pitcher"):
        g = g.sort_values("game_date").tail(5)
        if len(g) < 2:
            continue
        pa = g["pa"].sum()
        if pa:
            form[int(pid)] = float((g["k"].sum() - g["bb"].sum()) / pa)
    return form


def rest_days_asof(con, teams: set, asof: pd.Timestamp) -> dict:
    """team abbrev -> days since that team last played a final game."""
    rows = con.execute(
        "SELECT away, home, game_date FROM games WHERE sport='mlb'"
        " AND status='final' AND game_date < ?", (asof.strftime("%Y-%m-%d"),)
    ).fetchall()
    last = {}
    for r in rows:
        d = pd.Timestamp(r["game_date"])
        for name in (r["away"], r["home"]):
            ab = TEAM_ABBR.get(name)
            if ab and (ab not in last or d > last[ab]):
                last[ab] = d
    return {t: float((asof - last[t]).days) for t in teams if t in last}


def build_for_date(date: str | None = None, verbose: bool = True) -> int:
    date = date or dt.date.today().isoformat()
    asof = pd.Timestamp(date)

    sc = load_statcast()
    latest = sc["game_date"].max()
    stale_days = (asof - latest).days
    if verbose:
        print(f"Statcast loaded through {latest.date()} "
              f"({stale_days} day(s) before {date}).")
        if stale_days > STALE_WARN_DAYS:
            print(f"  WARNING: {stale_days} days stale. Rolling windows end at "
                  f"{latest.date()}, not {date}. Re-run backfill.py to refresh.")

    lines = pitcher_game_lines(sc)
    pen = bullpen_asof(lines, asof)
    off = offense_asof(sc, asof)
    sform = starters_asof(lines, asof)

    con = connect()
    games = con.execute(
        "SELECT * FROM games WHERE sport='mlb' AND game_date = ?"
        # NOT GLOB 'mlb-[0-9]*': an odds-API id like 'mlb-82b37a47...' starts
        # with a digit too. Require every character after 'mlb-' to be a digit,
        # which is what an MLB Stats API gamePk looks like.
        # status='scheduled' only, not "anything not final". A game already
        # under way is not a game anyone can bet, and building features for it
        # produced paper bets nobody could have placed - their CLV is exactly
        # 0 because the price used to place them is the same snapshot used to
        # grade them, so they padded the 50-bet floor with no information.
        " AND status = 'scheduled' AND SUBSTR(game_id,5) NOT GLOB '*[^0-9]*'",
        (date,)).fetchall()

    teams = set()
    for g in games:
        teams |= {TEAM_ABBR.get(g["away"]), TEAM_ABBR.get(g["home"])}
    teams.discard(None)
    rest = rest_days_asof(con, teams, asof)
    pf = park_factor_table(_history(con))

    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    written, skipped = 0, []
    for g in games:
        vec, why = _row(g, pen, off, sform, rest, pf, asof)
        if why:
            skipped.append((g["away"], g["home"], why))
            continue
        vec["_asof"] = date
        vec["_statcast_through"] = str(latest.date())
        vec["_stale_days"] = stale_days
        con.execute(
            # APPEND. The 11:30am feature row and the 10pm one are different
            # facts about different moments, and overwriting destroyed the
            # first one.
            "INSERT INTO features (game_id, sport, created_at, inputs_through,"
            " payload)"
            " VALUES (?,?,?,?,?)",
            (g["game_id"], "mlb", ts, str(latest.date()), json.dumps(vec)))
        written += 1
    con.commit()
    con.close()

    if verbose:
        print(f"Feature rows written: {written} of {len(games)} games on {date}.")
        for away, home, why in skipped:
            print(f"  skipped {away} @ {home}: {why}")
    return written


def _history(con) -> pd.DataFrame:
    """Final games, for park factors. Mirrors build_training.load_games."""
    g = pd.read_sql(
        "SELECT * FROM games WHERE sport='mlb' AND status='final'"
        " AND game_id LIKE 'mlb-%' AND game_id NOT LIKE 'mlb-espn-%'", con)
    g["game_date"] = pd.to_datetime(g["game_date"])
    g["home_ab"] = g["home"].map(TEAM_ABBR)
    g["away_ab"] = g["away"].map(TEAM_ABBR)
    g = g.dropna(subset=["home_ab", "away_ab", "home_score", "away_score"])
    g["season"] = g["game_date"].dt.year
    return g


def _row(g, pen, off, sform, rest, pf, asof):
    """One feature dict, or (None, reason) if the game cannot be scored."""
    ha, aa = TEAM_ABBR.get(g["home"]), TEAM_ABBR.get(g["away"])
    if not ha or not aa:
        return None, f"unmapped team name ({g['away']} / {g['home']})"
    hid, aid = g["home_starter_id"], g["away_starter_id"]
    if not hid or not aid:
        return None, "probable starter not announced"
    if int(hid) not in sform or int(aid) not in sform:
        missing = g["home_starter"] if int(hid) not in sform else g["away_starter"]
        return None, f"no recent start history for {missing}"
    if ha not in pen or aa not in pen or ha not in off or aa not in off:
        return None, "no recent bullpen/offense window for one side"
    if ha not in rest or aa not in rest:
        return None, "no prior game found for one side (rest days unknown)"

    hk, ak = sform[int(hid)], sform[int(aid)]
    venue = venue_id(ha, asof.year)
    vec = {
        "home_pen_kbb_30d": pen[ha][0], "home_pen_pitches_3d": pen[ha][1],
        "away_pen_kbb_30d": pen[aa][0], "away_pen_pitches_3d": pen[aa][1],
        "home_off_woba_30d": off[ha], "away_off_woba_30d": off[aa],
        "home_sp_kbb_5s": hk, "away_sp_kbb_5s": ak,
        "home_rest_days": rest[ha], "away_rest_days": rest[aa],
        "park_factor": pf.get((venue, asof.year), PARK_PRIORS.get(venue, 1.00)),
    }
    if any(v is None for v in vec.values()):
        return None, "a feature came back empty"
    return vec, None


if __name__ == "__main__":
    build_for_date(sys.argv[1] if len(sys.argv) > 1 else None)
