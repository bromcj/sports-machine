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
    if game_id.startswith("mlb-espn-"):
        return "espn"
    return "statsapi" if game_id[4:].isdigit() else "oddsfeed"


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

    rows = con.execute(
        f"SELECT DISTINCT g.game_id, g.start_time_utc FROM games g"
        f" WHERE g.sport=? AND g.away=? AND g.home=? AND ({SQL_ODDS_FEED})"
        f"   AND g.start_time_utc IS NOT NULL"
        f"   AND EXISTS (SELECT 1 FROM odds_snapshots o WHERE o.game_id=g.game_id)",
        (g["sport"], g["away"], g["home"])).fetchall()

    near = []
    for r in rows:
        other = parse_utc(r["start_time_utc"])
        if other is None:
            continue
        gap = abs((other - start).total_seconds()) / 60
        if gap <= MATCH_WINDOW_MIN:
            near.append((gap, r["game_id"]))
    if not near:
        return None, "no odds feed row within the match window"
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
