"""B3 dry run: the exact request plan for the NFL receiving-props backtest.

    python props/b3_dryrun.py               # free; spends nothing
    python props/b3_dryrun.py --write-plan  # also writes the request list

docs/experiments.md B3. This builds every request the backtest would make,
from the real 2023-2025 schedule, and adds up what they cost - WITHOUT making
any of them. Nothing here touches the odds API except one free events call used
to validate the team-name table.

WHAT DRIVES THE COST. Historical event odds are 10 credits per market per
event, measured (a two-market call came back at 20). There is no clustering
discount on that: props live on the per-EVENT endpoint, so 815 games is 815
requests however they are scheduled. The only place clustering saves anything
is the events-LIST call, at 1 credit each, which is noise by comparison.

THE ONE REAL DECISION the plan exposes: how many distinct snapshot timestamps
to use. NFL kickoffs cluster hard - 1:00, 4:05, 4:25 and 8:20 ET - so a
"60 minutes before kickoff" rule collapses to a handful of timestamps a week
rather than one per game, and event ids can be fetched once per snapshot and
reused. Both versions are costed below.

TEAM NAMES ARE VALIDATED HERE, NOT LATER. nflverse says "KC", the Odds API
says "Kansas City Chiefs", and a mismatch loses games silently. The Athletics
rename cost this project 169 games exactly that way. The check runs against a
live events list, which is free.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import paths
from ingest import http
from props.nfl_teams import to_name, to_abbr

API = "https://api.the-odds-api.com/v4"
SPORT = "americanfootball_nfl"
ET = ZoneInfo("America/New_York")

SEASONS = (2023, 2024, 2025)
MARKETS = ("player_receptions", "player_reception_yds")
LEAD_MIN = 60                  # snapshot this long before kickoff
OPEN_SEASON = 2025             # the Thursday-noon opening snapshot season
OPEN_WEEKDAY = 3               # Thursday
OPEN_HOUR_ET = 12

CREDITS_HIST_ODDS = 10         # per market, per event  (measured)
CREDITS_HIST_EVENTS = 1        # per request            (measured)
CAP = 55_000


def schedule() -> pd.DataFrame:
    p = paths.NFL_DIR / "schedules.parquet"
    if not p.exists():
        raise SystemExit(f"no schedule at {p}. Run: python backfill_nfl.py")
    s = pd.read_parquet(p)
    s = s[s["season"].isin(SEASONS) & (s["game_type"] == "REG")].copy()
    # gametime is local ET, gameday is the local date. Build an AWARE ET
    # timestamp and convert - never assume the naive string is UTC, which is
    # the mistake that once pushed every late game forward a day.
    stamp = s["gameday"].astype(str) + " " + s["gametime"].astype(str)
    local = pd.to_datetime(stamp, format="%Y-%m-%d %H:%M", errors="coerce")
    s["kickoff_utc"] = (local.dt.tz_localize(ET, nonexistent="shift_forward",
                                             ambiguous=True)
                             .dt.tz_convert("UTC"))
    bad = s["kickoff_utc"].isna().sum()
    if bad:
        print(f"  WARNING: {bad} games have an unparseable kickoff time")
    s = s.dropna(subset=["kickoff_utc"])
    s["snapshot_utc"] = s["kickoff_utc"] - pd.Timedelta(minutes=LEAD_MIN)
    return s.sort_values("kickoff_utc").reset_index(drop=True)


def validate_team_names(s: pd.DataFrame) -> bool:
    """Every abbreviation must map, and the names must be the API's own."""
    print("-" * 74)
    print("team names: nflverse abbreviation -> Odds API name")
    print("-" * 74)
    abbrs = sorted(set(s["home_team"]) | set(s["away_team"]))
    unmapped = [a for a in abbrs if to_name(a) is None]
    print(f"  {len(abbrs)} distinct abbreviations in {SEASONS}")
    if unmapped:
        print(f"  UNMAPPED: {unmapped}")
        return False
    print("  all map to a name")

    # Free call. The live events list is the API's own spelling, which is the
    # only authority that matters here.
    try:
        import os
        r = http.get(f"{API}/sports/{SPORT}/events",
                     params={"apiKey": os.environ.get("ODDS_API_KEY", "")},
                     label="events")
        cost = r.headers.get("x-requests-last")
        live = r.json()
    except Exception as e:
        print(f"  (could not fetch a live events list: {e}) - table unverified")
        return True

    api_names = set()
    for e in live:
        api_names |= {e["home_team"], e["away_team"]}
    print(f"  live events list: {len(live)} events, {len(api_names)} team "
          f"names, cost {cost} credit(s)")
    ours = {to_name(a) for a in abbrs}
    missing = api_names - ours
    unknown = {n for n in api_names if to_abbr(n) is None}
    if missing:
        print(f"  API uses names we do not produce: {sorted(missing)}")
    if unknown:
        print(f"  API names we cannot turn back into an abbreviation: "
              f"{sorted(unknown)}")
    ok = not missing and not unknown
    print("  " + ("every live API name round-trips through the table"
                  if ok else "TABLE IS INCOMPLETE - fix before spending"))
    return ok


def snapshot_plan(s: pd.DataFrame) -> pd.DataFrame:
    """One row per (snapshot timestamp, game). The requests we would make."""
    d = s[["season", "week", "game_id", "home_team", "away_team",
           "kickoff_utc", "snapshot_utc"]].copy()
    # The API stores snapshots on a 5-minute grid and resolves a `date` to the
    # nearest one, so timestamps that differ by a minute are the same request.
    d["slot"] = d["snapshot_utc"].dt.floor("5min")
    return d


def report(s: pd.DataFrame, plan: pd.DataFrame) -> dict:
    n_games = len(plan)
    n_markets = len(MARKETS)
    odds_credits = n_games * n_markets * CREDITS_HIST_ODDS

    slots = plan["slot"].nunique()
    days = plan["slot"].dt.date.nunique()

    print("\n" + "=" * 74)
    print("B3 DRY RUN - NFL receiving props, 2023-2025")
    print("=" * 74)
    print(f"\nmarkets: {', '.join(MARKETS)}")
    print(f"snapshot: {LEAD_MIN} minutes before kickoff\n")
    print(f"{'season':>7s} {'games':>7s} {'weeks':>7s} {'slots':>7s}")
    for season, g in plan.groupby("season"):
        print(f"{season:>7d} {len(g):>7,} {g['week'].nunique():>7d} "
              f"{g['slot'].nunique():>7d}")
    print(f"{'TOTAL':>7s} {n_games:>7,} {'':>7s} {slots:>7d}")

    print("\nkickoff slots are heavily clustered - the top ten cover most of "
          "the season:")
    tod = Counter(plan["kickoff_utc"].dt.strftime("%a %H:%MZ"))
    for k, v in tod.most_common(10):
        print(f"    {k}   {v:>4,} games")

    print("\n" + "-" * 74)
    print("CREDITS")
    print("-" * 74)
    print(f"  event odds   {n_games:,} games x {n_markets} markets x "
          f"{CREDITS_HIST_ODDS} = {odds_credits:>8,}")
    print(f"  events list, one per snapshot slot      {slots:>4d} x "
          f"{CREDITS_HIST_EVENTS} = {slots * CREDITS_HIST_EVENTS:>8,}")
    print(f"  events list, one per game DAY (reusing ids across the day's")
    print(f"               slots)                     {days:>4d} x "
          f"{CREDITS_HIST_EVENTS} = {days * CREDITS_HIST_EVENTS:>8,}")
    total_slot = odds_credits + slots * CREDITS_HIST_EVENTS
    total_day = odds_credits + days * CREDITS_HIST_EVENTS
    print(f"\n  TOTAL, a list call per slot   {total_slot:>8,}")
    print(f"  TOTAL, a list call per day    {total_day:>8,}   "
          f"(saves {total_slot - total_day})")
    print("\n  The saving is trivial next to the odds calls, so take the")
    print("  per-slot version: it re-reads the event list at the moment it is")
    print("  about to price, instead of assuming an id fetched hours earlier")
    print("  is still the right one.")

    # --- the opening snapshot ---
    o = plan[plan["season"] == OPEN_SEASON]
    open_weeks = o["week"].nunique()
    open_credits = len(o) * n_markets * CREDITS_HIST_ODDS
    print("\n" + "-" * 74)
    print(f"OPENING SNAPSHOT, {OPEN_SEASON} only "
          f"(Thursday {OPEN_HOUR_ET}:00 ET, for the movement test)")
    print("-" * 74)
    print(f"  {len(o):,} games over {open_weeks} weeks")
    print(f"  event odds   {len(o):,} x {n_markets} x {CREDITS_HIST_ODDS} = "
          f"{open_credits:>8,}")
    print(f"  events list  {open_weeks} x {CREDITS_HIST_EVENTS} = "
          f"{open_weeks * CREDITS_HIST_EVENTS:>8,}")
    print(f"  TOTAL {open_credits + open_weeks:>8,}")

    print("\n" + "=" * 74)
    print("GRAND TOTAL")
    print("=" * 74)
    print(f"  closing snapshot, 2023-2025   {total_slot:>8,}")
    print(f"  opening snapshot, {OPEN_SEASON} only    "
          f"{open_credits + open_weeks:>8,}")
    print(f"  {'':30s}{'-' * 8}")
    print(f"  {'':30s}{total_slot + open_credits + open_weeks:>8,}")
    print(f"\n  cap {CAP:,}   "
          f"remaining after: "
          f"{58_442 - (total_slot + open_credits + open_weeks):,} of the "
          f"58,442 credits left this month")
    return {"games": n_games, "slots": slots, "closing": total_slot,
            "opening": open_credits + open_weeks,
            "total": total_slot + open_credits + open_weeks}


def risks(plan: pd.DataFrame) -> None:
    print("\n" + "=" * 74)
    print("WHAT THIS DRY RUN CANNOT TELL YOU")
    print("=" * 74)
    print("""
  1. WHETHER PROPS EXIST AT THURSDAY NOON FOR SUNDAY GAMES.
     The opening-snapshot line is 5,440 credits and rests on an assumption
     the dry run cannot test: that books had posted receiving props by
     Thursday lunchtime. Props are widely said to go up Tuesday-Thursday, but
     "said to" is not a measurement, and B2 already found books PULLING prop
     markets at first pitch - so their posting behaviour is not something to
     guess at.

     Cost to settle it: ONE historical event-odds call at a 2025 Thursday
     noon for a Sunday game. 10 credits for one market, 20 for both. Worth
     doing before committing the 5,440.

  2. HOW MANY PLAYERS PER GAME ACTUALLY GET PRICED.
     The 2023 probe returned 26 outcomes per market at DraftKings and 4 at
     BetMGM. If the median game carries ~12 priced receivers, 815 games is
     roughly 10,000 gradeable props - a good sample. If it is 6, it is half
     that. This changes the POWER of the backtest, not its price, so it does
     not block the spend.

  3. WHETHER PLAYER NAMES MATCH nflverse gsis_id.
     The Odds API gives a player's display name; nflverse gives an id. That
     join is by name plus team and it will not be perfect. B3 reports the
     match rate and the unmatched names rather than dropping them silently -
     the same rule the Athletics bug produced.
""".rstrip())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-plan", action="store_true",
                    help="write the full request list to docs/results/")
    args = ap.parse_args()

    s = schedule()
    ok = validate_team_names(s)
    plan = snapshot_plan(s)
    summary = report(s, plan)
    risks(plan)

    if not ok:
        print("\nTEAM TABLE INCOMPLETE - do not spend until it round-trips.")
    if args.write_plan:
        out = ROOT / "docs" / "results" / "b3_request_plan.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        plan.assign(markets=",".join(MARKETS)).to_csv(out, index=False)
        print(f"\nrequest plan -> {out}  ({len(plan):,} rows)")
        (out.parent / "b3_summary.json").write_text(
            json.dumps(summary, indent=2))
    print("\nNOTHING WAS SPENT. This was a dry run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
