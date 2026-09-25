"""Sportsbook prices, from The Odds API's /odds payload, into markets and prices.

One event from the API carries every named book's h2h, spreads and totals.
Each (book, market, line) becomes one market; each side becomes one price.

  h2h      outcomes 'home' / 'away', no line
  spread   outcomes 'home' / 'away', line = the HOME side's point
  total    outcomes 'over' / 'under', line = the total

`price` is 1 / decimal odds - the price paid, vig included. scanner.fair takes
the vig out of the fair side; nothing takes it out of what a bettor pays.

The game itself is the odds feed's row, `<sport>-<event id>` - the id
ingest/odds.py already mints for the same event, so the scanner and the
cloud's pulls agree on it.
"""
import datetime as dt

from bets.engine import american_to_decimal
from feeds import parse_utc
from scanner.store import canon_ts, market_key
from scanner.venues import book_venue

FEE_MODEL = "book"

# Roughly how long a game lasts, so a position knows when its money comes
# back. Capital accounting only; nothing is graded on it.
GAME_HOURS = {"nfl": 3.5, "nba": 2.5, "nhl": 2.75, "mlb": 3.25}

TYPES = {"h2h": "h2h", "spreads": "spread", "totals": "total"}


def game_id(sport: str, event: dict) -> str:
    return f"{sport}-{event['id']}"


def resolves_at(sport: str, start: dt.datetime) -> str:
    return canon_ts(start + dt.timedelta(hours=GAME_HOURS.get(sport, 3.5)))


def _sides(mkt: str, outcomes: list, away: str, home: str):
    """[(outcome, american price, point)] in canonical terms, or a reason."""
    got = {}
    for o in outcomes:
        name = str(o.get("name", ""))
        if mkt == "totals":
            side = name.strip().lower()
            if side not in ("over", "under"):
                return None, f"total outcome {name!r} is not Over/Under"
        elif name == home:
            side = "home"
        elif name == away:
            side = "away"
        else:
            return None, f"outcome {name!r} names neither team"
        if side in got:
            return None, f"two {side} outcomes"
        got[side] = (o.get("price"), o.get("point"))
    want = ("over", "under") if mkt == "totals" else ("home", "away")
    if set(got) != set(want):
        return None, f"{mkt} needs both of {want}, got {sorted(got)}"
    if mkt == "spreads":
        h, a = got["home"][1], got["away"][1]
        if h is None or a is None or float(h) != -float(a):
            return None, f"spread points do not mirror ({h} / {a})"
    if mkt == "totals" and got["over"][1] != got["under"][1]:
        return None, "over and under are at different totals"
    return [(s, got[s][0], got[s][1]) for s in want], None


def rows(sport: str, events: list, captured_at, raw_ref: str | None = None,
         rejects=None):
    """(games, markets, prices) from one /odds response.

    Anything that cannot be made into a clean row is counted in `rejects`
    (an ingest.quality.Rejects) rather than guessed at.
    """
    captured = canon_ts(captured_at)
    games, markets, prices = [], [], []
    for ev in events:
        start = parse_utc(ev.get("commence_time"))
        away, home = ev.get("away_team"), ev.get("home_team")
        if start is None or not away or not home:
            if rejects is not None:
                rejects.check("event has no start time or no teams")
            continue
        gid = game_id(sport, ev)
        games.append({"game_id": gid, "sport": sport,
                      "commence_time": ev["commence_time"],
                      "away": away, "home": home})
        for bk in ev.get("bookmakers", []):
            venue = book_venue(bk["key"])
            for mkt in bk.get("markets", []):
                mtype = TYPES.get(mkt.get("key"))
                if mtype is None:
                    continue
                sides, why = _sides(mkt["key"], mkt.get("outcomes", []), away, home)
                if sides is None:
                    if rejects is not None:
                        rejects.check(f"{bk['key']} {mkt['key']}: {why}")
                    continue
                line = None
                if mtype == "spread":
                    line = float(sides[0][2])            # the home side's point
                elif mtype == "total":
                    line = float(sides[0][2])
                mid = market_key(venue, ev["id"], mtype, line)
                markets.append({
                    "market_id": mid, "venue": venue, "venue_market_id": ev["id"],
                    "sport": sport, "canonical_event_id": gid,
                    "market_type": mtype, "line": line, "yes_outcome": None,
                    "event_start": canon_ts(start),
                    "resolves_at": resolves_at(sport, start),
                    "resolution_source": "final score",
                    "first_seen": captured})
                last = mkt.get("last_update") or bk.get("last_update")
                for side, ml, _point in sides:
                    try:
                        dec = american_to_decimal(int(ml))
                    except (TypeError, ValueError, ZeroDivisionError):
                        dec = None
                    prices.append({
                        "market_id": mid, "venue": venue, "outcome": side,
                        "quote": "ask", "level": 1,
                        "price": (1 / dec) if dec and dec > 1 else None,
                        "price_native": "" if ml is None else str(ml),
                        "size_available": None, "fee_model": FEE_MODEL,
                        "captured_at": captured,
                        "source_last_update": canon_ts(last) if parse_utc(last) else None,
                        "raw_ref": raw_ref})
    return games, markets, prices


def write(con, sport: str, events: list, captured_at, raw_ref: str | None = None,
          rejects=None) -> dict:
    """Store one /odds response: games (the same rows ingest/odds.py writes),
    markets and prices. The caller commits. Returns counts."""
    from ingest import quality
    from ingest.odds import upsert_game
    from scanner import store
    rejects = rejects if rejects is not None else quality.Rejects(f"{sport} scan")
    games, markets, prices = rows(sport, events, captured_at, raw_ref, rejects)
    kept = {g["game_id"] for g in games
            if upsert_game(con, g["game_id"], sport, g["commence_time"],
                           g["away"], g["home"], rejects)}
    markets = [m for m in markets if m["canonical_event_id"] in kept]
    ids = {m["market_id"] for m in markets}
    n_m = store.upsert_markets(con, markets, rejects)
    n_p = store.insert_prices(con, [p for p in prices if p["market_id"] in ids],
                              rejects)
    return {"games": len(kept), "markets": n_m, "prices": n_p,
            "rejected": len(rejects)}
