"""Does a Thursday-noon prop market exist for Sunday's games? ~21 credits.

    python props/check_thursday.py --spend 25

B3's opening snapshot is 5,440 credits and rests on one unverified assumption:
that books had posted receiving props by Thursday lunchtime for games three
days away. Props are widely said to go up Tuesday-Thursday, but B2 already
found books PULLING prop markets at first pitch, so their posting behaviour is
observed here rather than assumed.

The test is deliberately the real thing, not a proxy: the exact request the
opening snapshot would make, at a real 2025 Thursday noon ET, for a real Sunday
game, for both markets the plan buys.

    historical events list      1 credit
    historical event odds      10 credits per market  => 20 for the two

Two Thursdays are checked rather than one, because a single week could be a
holiday, a bye-heavy week, or simply an outlier, and "the opening snapshot does
not exist" is a 5,440-credit conclusion either way. --spend caps the total and
the script stops rather than exceeding it.

WHAT COUNTS AS A PASS. Not "some book returned something". The movement test
needs a fair line at BOTH ends, and B2 established that the fair line for NFL
props is Pinnacle. So the question is specifically whether a sharp price exists
on Thursday, and how many books and players come with it.

----------------------------------------------------------------------------
RESULT, run 2026-09-24 on 2025 weeks 7 and 13. 42 credits.

  Sunday's games ARE listed on Thursday.     both weeks
  Props DO exist by Thursday noon.           but thinly and unevenly:
      week 7   3 books,  4 players priced
      week 13  7 books, 13 players priced
  PINNACLE HAD POSTED NOTHING.               0 players, both weeks

So the Thursday snapshot has no sharp anchor. Measuring a move from a
soft-consensus open to a Pinnacle close is two different rulers, which is the
same class of mistake as A1's shared-denominator artifact and would push the
movement slope in an unknown direction.

The second Thursday earned its 21 credits. Week 7 alone (3 books, 4 players)
reads as "props barely exist on Thursday"; week 13 shows they mostly do. One
sample would have produced a confident wrong answer in either direction.
"""
import argparse
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import paths
from ingest import http
from props.preflight import BOOKS, API

SPORT = "americanfootball_nfl"
ET = ZoneInfo("America/New_York")
MARKETS = ("player_receptions", "player_reception_yds")
SHARP = "pinnacle"


def _key() -> str:
    """The persisted user key, not whatever this process inherited.

    The Claude Code session that wrote this file had inherited a STALE
    ODDS_API_KEY - the free 500/month one - because the process started before
    the paid key was set. Four credits went to the wrong account before anyone
    noticed. Reading the persisted value directly removes that whole class of
    mistake on Windows; elsewhere the environment is the environment.
    """
    if sys.platform == "win32":
        try:
            out = subprocess.run(
                ["reg", "query", r"HKCU\Environment", "/v", "ODDS_API_KEY"],
                capture_output=True, text=True).stdout
            for line in out.splitlines():
                if "REG_SZ" in line:
                    v = line.split("REG_SZ")[-1].strip()
                    if v:
                        return v
        except Exception:
            pass
    k = os.environ.get("ODDS_API_KEY", "")
    if not k:
        raise SystemExit("ODDS_API_KEY is not set.")
    return k


def thursday_before(kickoff_local: pd.Timestamp) -> dt.datetime:
    """Noon ET on the Thursday preceding a Sunday kickoff, as aware UTC."""
    d = kickoff_local.date()
    # Sunday is weekday 6; the Thursday before it is 3 days earlier.
    thurs = d - dt.timedelta(days=(d.weekday() - 3) % 7 or 7)
    local = dt.datetime.combine(thurs, dt.time(12, 0), tzinfo=ET)
    return local.astimezone(dt.timezone.utc)


def pick_sundays(n: int = 2) -> list:
    """A few mid-season 2025 Sunday afternoon games, spread across the year."""
    s = pd.read_parquet(paths.NFL_DIR / "schedules.parquet")
    s = s[(s["season"] == 2025) & (s["game_type"] == "REG")
          & (s["weekday"] == "Sunday")].copy()
    stamp = s["gameday"].astype(str) + " " + s["gametime"].astype(str)
    s["local"] = pd.to_datetime(stamp, format="%Y-%m-%d %H:%M",
                                errors="coerce")
    s = s.dropna(subset=["local"])
    # the 1pm ET window, which is where most of the sample lives
    s = s[s["local"].dt.hour == 13]
    weeks = sorted(s["week"].unique())
    picks = []
    for w in (weeks[len(weeks) // 3], weeks[2 * len(weeks) // 3])[:n]:
        picks.append(s[s["week"] == w].iloc[0])
    return picks


def probe(game, spent: dict, cap: int) -> dict:
    when = thursday_before(game["local"])
    iso = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    kick = game["local"]
    print("\n" + "-" * 74)
    print(f"week {int(game['week'])}: {game['away_team']} @ "
          f"{game['home_team']}")
    print(f"  kickoff  {kick} ET (Sunday)")
    print(f"  snapshot {iso}  (Thursday 12:00 ET, "
          f"{(kick.tz_localize(ET) - when).total_seconds() / 3600:.0f} h "
          f"before kickoff)")

    if spent["credits"] + 1 > cap:
        print("  budget exhausted before the events list")
        return {}
    r = http.get(f"{API}/historical/sports/{SPORT}/events",
                 params={"apiKey": _key(), "date": iso}, label="hist events")
    spent["credits"] += int(r.headers.get("x-requests-last") or 0)
    body = r.json()
    evs = body.get("data", body)
    print(f"  events list: {len(evs)} events visible, snapshot "
          f"{body.get('timestamp')}, cost "
          f"{r.headers.get('x-requests-last')} credit(s)")

    want_home = game["home_team"]
    from props.nfl_teams import to_name
    target_home = to_name(want_home)
    match = [e for e in evs if e.get("home_team") == target_home]
    if not match:
        print(f"  Sunday's game ({target_home}) is NOT in the Thursday events "
              f"list at all")
        print("  -> the opening snapshot cannot be built for this week")
        return {"listed": False}
    ev = match[0]
    print(f"  the Sunday game IS listed (id {ev['id'][:12]}...)")

    need = 10 * len(MARKETS)
    if spent["credits"] + need > cap:
        print(f"  budget would be exceeded by the odds call ({need}); "
              f"stopping")
        return {"listed": True, "priced": None}
    r2 = http.get(f"{API}/historical/sports/{SPORT}/events/{ev['id']}/odds",
                  params={"apiKey": _key(), "date": iso, "bookmakers": BOOKS,
                          "markets": ",".join(MARKETS),
                          "oddsFormat": "american"}, label="hist props")
    cost = int(r2.headers.get("x-requests-last") or 0)
    spent["credits"] += cost
    spent["remaining"] = r2.headers.get("x-requests-remaining")
    data = r2.json().get("data", {})
    books = data.get("bookmakers", [])
    print(f"  event odds: cost {cost} credit(s), "
          f"{spent['remaining']} remaining")

    if not books:
        print("  NO BOOK had posted either market by Thursday noon.")
        return {"listed": True, "priced": False, "books": 0}

    players, per_book = set(), {}
    sharp_players = set()
    for b in books:
        got = {}
        for m in b.get("markets", []):
            names = {o.get("description") for o in m.get("outcomes", [])}
            got[m["key"]] = len(m.get("outcomes", []))
            players |= names
            if b["key"] == SHARP:
                sharp_players |= names
        per_book[b["key"]] = got
    print(f"  {len(books)} books posted something:")
    for bk, got in sorted(per_book.items()):
        mark = "  <- sharp" if bk == SHARP else ""
        print(f"    {bk:14s} " + "  ".join(
            f"{k}={v}" for k, v in sorted(got.items())) + mark)
    print(f"  distinct players priced: {len(players)}"
          f"   at Pinnacle: {len(sharp_players)}")
    return {"listed": True, "priced": True, "books": len(books),
            "players": len(players), "sharp_players": len(sharp_players),
            "has_sharp": SHARP in per_book}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spend", type=int, default=25,
                    help="hard credit cap for the whole check")
    args = ap.parse_args()

    print("=" * 74)
    print("Does a Thursday-noon prop market exist for Sunday's games?")
    print(f"markets: {', '.join(MARKETS)}   hard cap: {args.spend} credits")
    print("=" * 74)

    spent = {"credits": 0, "remaining": None}
    results = []
    for game in pick_sundays(2):
        results.append(probe(game, spent, args.spend))
        if spent["credits"] >= args.spend:
            break

    print("\n" + "=" * 74)
    print("VERDICT")
    print("=" * 74)
    priced = [r for r in results if r.get("priced")]
    sharp = [r for r in results if r.get("has_sharp")]
    if not results or not any(r.get("listed") for r in results):
        print("  Sunday's games are not even LISTED on Thursday. The opening")
        print("  snapshot does not exist. Drop that 5,440 from the plan.")
    elif not priced:
        print("  Games are listed but NO book has posted receiving props by")
        print("  Thursday noon. The opening snapshot would buy empty")
        print("  responses. Drop the 5,440, or move the snapshot later and")
        print("  re-check.")
    elif not sharp:
        print("  Props exist on Thursday but PINNACLE has not posted them.")
        print("  The movement test needs a fair line at both ends and B2")
        print("  established that is Pinnacle for NFL props, so the opening")
        print("  snapshot would be measured against a soft consensus at one")
        print("  end and a sharp line at the other - two different rulers.")
        print("  Worth doing only with that caveat recorded on every row.")
    else:
        n = [r.get("sharp_players", 0) for r in sharp]
        print("  Props DO exist at Thursday noon, and Pinnacle prices them.")
        print(f"  Pinnacle priced {min(n)}-{max(n)} players per game.")
        print("  The opening snapshot is real. The 5,440 line stands.")
    print(f"\nspent {spent['credits']} credit(s), "
          f"{spent['remaining']} remaining")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
