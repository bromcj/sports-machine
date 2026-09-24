"""Which feed a game_id came from, and how to match the same game across feeds.

Three feeds mint three different ids for one game, and none of them agree:

    MLB Stats API   mlb-823494
    The Odds API    mlb-394e1e2b849c5b4eaf2b5984eaa74b79
    ESPN            mlb-espn-401817028

Everything used to be matched on (game_date, away, home). That is broken, and
not subtly: the odds API and ESPN date a game by its UTC date while the Stats
API dates it locally, so every game starting after 8pm ET carries a date one
day ahead. In a series, Wednesday's game then matched TUESDAY's prices.
Measured before this module existed: 5 of 20 matched games were wrong, all of
them late west-coast starts - which are exactly the games whose closing prices
qualify for CLV grading.

So matching is on TIME: same teams, first pitch within MATCH_WINDOW_MIN. That
is what makes two rows the same game, and it resolves doubleheaders by their
start times instead of refusing them.

Identifying a feed by its id shape also lived in several places, each written
slightly differently. `GLOB 'mlb-[0-9]*'` looks right and is wrong - odds ids
are hex and often start with a digit, so 'mlb-85b75fca...' passes it. That
mistake counted 23 scheduled games where there were 15. One definition here.
"""
import datetime as dt

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:                                   # no tz database installed
    ET = dt.timezone(dt.timedelta(hours=-4))

# How far apart two feeds' first-pitch times may be and still be one game.
# Feeds disagree by minutes over scheduled-vs-actual first pitch; they never
# disagree by hours. Comfortably tighter than the gap between two games of a
# doubleheader, which is what lets this tell them apart.
MATCH_WINDOW_MIN = 180

# SQL fragments, so the id-shape rules exist once.
SQL_ESPN = "game_id LIKE 'mlb-espn-%'"
SQL_STATS_API = ("game_id NOT LIKE 'mlb-espn-%'"
                 " AND SUBSTR(game_id,5) NOT GLOB '*[^0-9]*'")
SQL_ODDS_FEED = ("game_id NOT LIKE 'mlb-espn-%'"
                 " AND SUBSTR(game_id,5) GLOB '*[^0-9]*'")


def feed_of(game_id: str) -> str:
    """Which feed minted this id. Sport-agnostic: 'nfl-espn-...' is ESPN too."""
    rest = game_id.split("-", 1)[1] if "-" in game_id else game_id
    if rest.startswith("espn-"):
        return "espn"
    return "statsapi" if rest.isdigit() else "oddsfeed"


def parse_utc(value: str | None) -> dt.datetime | None:
    """Any stored timestamp -> aware UTC datetime. None if unusable."""
    if not value:
        return None
    try:
        when = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)


def et_date(value) -> str | None:
    """The LOCAL (Eastern) calendar date a game belongs to.

    This is what game_date means everywhere. A 10:11pm ET first pitch is
    02:11 UTC the NEXT day, and dating it by UTC is the whole bug.
    """
    when = parse_utc(value)
    return when.astimezone(ET).date().isoformat() if when else None


# Clubs the two feeds spell differently, and when.
#
# The Athletics dropped "Oakland" for 2025. The MLB Stats API changed with
# them; the odds feed kept "Oakland Athletics" for that season and only
# switched for 2026. So for 2025 ONLY, the same club has two names, and a
# match on the raw string silently lost every Athletics game - 169 of them,
# 7% of the season, and a distinctive team (poor, in a hitter-friendly
# temporary park) whose absence would not be neutral.
#
# Matching canonicalises the name rather than the abbreviation, so feeds.py
# stays independent of the feature modules.
TEAM_ALIASES = {
    "oakland athletics": "athletics",
}


def canon_team(name) -> str:
    n = " ".join(str(name or "").split()).lower()
    return TEAM_ALIASES.get(n, n)


def odds_twin(con, game_id: str) -> tuple[str | None, str | None]:
    """The odds-feed row for the same real game. Returns (game_id, note).

    Matched on teams plus first-pitch time. Both sides need start_time_utc;
    without it there is no honest way to tell two games of a series apart, so
    this refuses rather than guessing - the guess is what produced the bug.
    """
    g = con.execute(
        "SELECT game_id, sport, away, home, start_time_utc FROM games"
        " WHERE game_id=?", (game_id,)).fetchone()
    if g is None:
        return None, "no such game_id"
    start = parse_utc(g["start_time_utc"])
    if start is None:
        return None, "no start_time_utc on this game - cannot match by time"

    # Candidates by TIME in SQL, then teams in Python on canonical names -
    # a raw string comparison cannot survive a club being renamed mid-history.
    lo = (start - dt.timedelta(minutes=MATCH_WINDOW_MIN)).isoformat()
    hi = (start + dt.timedelta(minutes=MATCH_WINDOW_MIN)).isoformat()
    rows = con.execute(
        f"SELECT DISTINCT g.game_id, g.away, g.home, g.start_time_utc FROM games g"
        f" WHERE g.sport=? AND ({SQL_ODDS_FEED})"
        f"   AND g.start_time_utc IS NOT NULL"
        f"   AND g.start_time_utc >= ? AND g.start_time_utc <= ?"
        f"   AND EXISTS (SELECT 1 FROM odds_snapshots o WHERE o.game_id=g.game_id)",
        (g["sport"], lo, hi)).fetchall()

    want = (canon_team(g["away"]), canon_team(g["home"]))

    near = []
    for r in rows:
        if (canon_team(r["away"]), canon_team(r["home"])) != want:
            continue
        other = parse_utc(r["start_time_utc"])
        if other is None:
            continue
        gap = abs((other - start).total_seconds()) / 60
        if gap <= MATCH_WINDOW_MIN:
            near.append((gap, r["game_id"]))
    if not near:
        return None, "no odds feed row within the match window"

    # A straight doubleheader: the same teams, from the SAME feed, starting
    # inside the window too. The Stats API lists game 2 at game 1's time plus
    # five minutes until game 1 ends, so both games found game 1's odds row -
    # 23 odds rows in market_close were claimed by two games each. There is no
    # honest way to tell them apart by time, so refuse rather than guess.
    same_feed = {"statsapi": SQL_STATS_API,
                 "espn": SQL_ESPN}.get(feed_of(game_id))
    for s in ([] if same_feed is None else con.execute(
            f"SELECT game_id, away, home, start_time_utc FROM games g"
            f" WHERE g.sport=? AND ({same_feed}) AND g.game_id != ?"
            f"   AND g.start_time_utc >= ? AND g.start_time_utc <= ?",
            (g["sport"], game_id, lo, hi)).fetchall()):
        other = parse_utc(s["start_time_utc"])
        if ((canon_team(s["away"]), canon_team(s["home"])) == want
                and other is not None
                and abs((other - start).total_seconds()) / 60 <= MATCH_WINDOW_MIN):
            return None, (f"ambiguous: doubleheader ({s['game_id']} starts "
                          f"within {MATCH_WINDOW_MIN} min)")

    near.sort()
    # Two candidates inside the window means the window is too wide for this
    # slate, not that the game is ambiguous. Say so plainly rather than guess.
    if len(near) > 1 and near[1][0] - near[0][0] < 1:
        return None, f"ambiguous: {len(near)} odds rows at the same start time"
    return near[0][1], None


def pregame_books(con, game_id: str):
    """Latest PREGAME price per book for this game. Returns (rows, note).

    The odds API keeps serving a market after first pitch, switching to in-play
    prices, so taking the newest snapshot per book grabs a live line once a
    game is under way: a real pull at 12:24am returned Giants -10000, a 97.1%
    "market probability", because they were already winning. Only snapshots
    taken strictly before first pitch are market opinions.
    """
    twin, note = odds_twin(con, game_id)
    if twin is None:
        # The id handed in may already BE the odds-feed row.
        if feed_of(game_id) == "oddsfeed":
            twin = game_id
        else:
            return [], note

    rows = con.execute(
        "SELECT * FROM odds_snapshots WHERE game_id=? AND away_ml IS NOT NULL",
        (twin,)).fetchall()
    latest = {}
    for r in rows:
        start = parse_utc(r["commence_time"])
        taken = parse_utc(r["ts"])
        if start is None or taken is None or taken >= start:
            continue
        prev = latest.get(r["book"])
        if prev is None or taken > prev[0]:
            latest[r["book"]] = (taken, r)
    if not latest:
        return [], "only in-play prices (game already started)"
    return [r for _, r in latest.values()], None


# ---------------------------------------------------------------- status ---
#
# Both feeds call a postponed game "final", each in its own way, and the code
# believed them. ESPN reports a postponement with state 'post' and no score,
# which scores.py turned into 0-0: the archive holds TOR @ BAL 2026-09-22 as a
# 0-0 FINAL, which cannot happen in MLB. The Stats API is worse, because
# abstractGameState is literally "Final" while detailedState says "Postponed".
#
# Because final is terminal, the make-up game - same gamePk, new date - could
# then never get its score or its real date. Two rows in this database were
# stuck that way: mlb-823543 dated 2026-05-23 with a September start, and
# mlb-824785 dated a day before the game it describes.
#
# So status is read from the DETAILED field, and only a real completion is
# allowed to be final.
TERMINAL = "final"

_STATS_DETAIL = {
    "postponed": "postponed",
    "suspended": "suspended",
    "cancelled": "cancelled",
    "canceled": "cancelled",
}

_ESPN_NAME = {
    "STATUS_SCHEDULED": "scheduled", "STATUS_IN_PROGRESS": "live",
    "STATUS_FINAL": "final", "STATUS_POSTPONED": "postponed",
    "STATUS_SUSPENDED": "suspended", "STATUS_CANCELED": "cancelled",
    "STATUS_CANCELLED": "cancelled", "STATUS_DELAYED": "scheduled",
    "STATUS_RAIN_DELAY": "live", "STATUS_END_PERIOD": "live",
}


def stats_api_status(status: dict) -> str:
    """MLB Stats API status block -> scheduled|live|final|postponed|... ."""
    detail = str(status.get("detailedState", "")).strip().lower()
    for key, mapped in _STATS_DETAIL.items():
        if key in detail:
            return mapped
    abstract = str(status.get("abstractGameState", "")).strip().lower()
    if abstract == "final":
        return "final"
    if abstract == "live":
        return "live"
    return "scheduled"


def espn_status(status_type: dict) -> str:
    """ESPN status.type block -> the same vocabulary."""
    name = str(status_type.get("name", "")).strip().upper()
    if name in _ESPN_NAME:
        return _ESPN_NAME[name]
    # Fall back to the coarse state, but never let it invent a final.
    state = str(status_type.get("state", "")).strip().lower()
    if state == "in":
        return "live"
    if state == "post":
        return "final"
    return "scheduled"


def slot_or_none(value):
    """ET slate slot for a first-pitch instant, or None if unusable."""
    from model.validation import slot_of
    when = parse_utc(value)
    return slot_of(when.astimezone(ET).hour) if when else None
