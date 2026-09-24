"""Compute the flags bets/engine.py has always been able to refuse on.

evaluate() supports sp_unconfirmed, opener_flag and pitch_limit_flag, and its
docstring promises those protections. No caller ever passed any of them, so no
guardrail could fire and the promise was empty.

Each flag is a REASON TO DECLINE, not a feature. The model does not see them.
They exist because the price you are betting into already reflects a confirmed
starter, and if yours is not confirmed - or is an opener who will throw two
innings - then you and the market are pricing different games.

All of this reads the local database and costs nothing.
"""
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from feeds import parse_utc

# Under this many innings per recent start, the "starter" is an opener. The
# model's sp_kbb_5s describes a pitcher who will face a lineup twice; an
# opener faces it once, and the bullpen pitches the rest.
OPENER_MAX_IP = 2.0
OPENER_MIN_STARTS = 3


def flags_for_game(con, game_id: str, side: str, now=None) -> dict:
    """Guardrail flags for betting `side` of this game."""
    now = now or dt.datetime.now(dt.timezone.utc)
    g = con.execute(
        "SELECT away_starter, home_starter, away_starter_id, home_starter_id,"
        " start_time_utc FROM games WHERE game_id=?", (game_id,)).fetchone()
    if g is None:
        return {"sp_unconfirmed": True}

    # The starter you are betting FOR. A mystery opposing starter is the other
    # side's problem; an unconfirmed one on your own side is yours.
    name = g["home_starter"] if side == "home" else g["away_starter"]
    pid = g["home_starter_id"] if side == "home" else g["away_starter_id"]
    out = {"sp_unconfirmed": not name or pid is None}

    # A game already under way cannot be bet: treat its starter as unconfirmed.
    start = parse_utc(g["start_time_utc"])
    if start is not None and not out["sp_unconfirmed"]:
        hours = (start - now).total_seconds() / 3600
        if hours < 0:
            out["sp_unconfirmed"] = True          # already started
    out["opener_flag"] = _is_opener(pid)
    return out


def _is_opener(pitcher_id) -> bool:
    """Has this pitcher been going fewer than OPENER_MAX_IP innings per start?

    Measured from Statcast rather than a list of names, so it needs no
    maintenance and catches a starter who becomes an opener mid-season.
    """
    if pitcher_id is None:
        return False
    try:
        from features.sports.mlb_features import load_statcast, pitcher_game_lines
        sc = load_statcast(years=[dt.date.today().year])
    except Exception:
        return False                              # no data: do not invent a flag
    lines = pitcher_game_lines(sc)
    mine = lines[(lines["pitcher"] == int(pitcher_id))
                 & lines["is_starter"]].sort_values("game_date").tail(5)
    if len(mine) < OPENER_MIN_STARTS:
        return False
    return bool(innings_per_start(sc, int(pitcher_id), mine["game_pk"])
                < OPENER_MAX_IP)


# Outs recorded by each plate-appearance outcome. Batters faced / 3 counted
# every hit and walk as a third of an inning and overstated innings by ~40%:
# it gave Richard Lovelady 2.07 innings a start (not an opener) where these
# outs give 1.40, and Framber Valdez 8.2 where they give 5.9. Outs made on the
# bases (caught stealing, pickoffs) are not plate appearances and are missed,
# which errs toward flagging.
OUTS = {"field_out": 1, "strikeout": 1, "force_out": 1, "sac_fly": 1,
        "sac_bunt": 1, "fielders_choice_out": 1, "grounded_into_double_play": 2,
        "double_play": 2, "strikeout_double_play": 2, "sac_fly_double_play": 2,
        "triple_play": 3}


def innings_per_start(sc, pitcher_id: int, game_pks) -> float:
    """Average innings over those games, from the outs this pitcher recorded."""
    ev = sc[(sc["pitcher"] == pitcher_id) & sc["game_pk"].isin(list(game_pks))
            & sc["events"].notna()]
    n = len(set(game_pks))
    return ev["events"].map(OUTS).fillna(0).sum() / 3.0 / n if n else 0.0


def daily_exposure(con, sport: str, date: str, bankroll: float) -> float:
    """Fraction of bankroll already staked on `date`. MAX_DAILY_PCT was unused.

    A per-bet cap says nothing about a slate. Fifteen bets at the 3% per-bet
    maximum is 45% of the bankroll on one night's baseball, which is not a
    staking plan, it is a coin flip with extra steps.
    """
    row = con.execute(
        "SELECT COALESCE(SUM(b.stake), 0) s FROM bets b"
        " JOIN games g ON g.game_id = b.game_id"
        " WHERE b.mode='real' AND b.sport=? AND g.game_date=?",
        (sport, date)).fetchone()
    return (row["s"] / bankroll) if bankroll else 0.0
