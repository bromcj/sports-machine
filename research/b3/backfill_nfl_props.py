"""B3: buy the NFL receiving-props closing snapshots. 2023-2025. ~16,700 credits.

    python props/backfill_nfl_props.py                       # dry run (free)
    python props/backfill_nfl_props.py --execute --max-credits 17000

Priced by props/b3_dryrun.py: 816 regular-season games, 374 snapshot slots,
2 markets, 10 credits per market per event.

    events list   374 x  1 =    374
    event odds    816 x 20 = 16,320
                             ------
                             16,694

THE OPENING SNAPSHOT IS NOT PART OF THIS. props/check_thursday.py measured
2025 weeks 7 and 13 and found that although receiving props do exist by
Thursday noon, PINNACLE HAS NOT POSTED THEM - 0 players, both weeks. A movement
test from a soft-consensus open to a Pinnacle close is two different rulers,
so that 5,458 is deferred until a properly-timed opener can be found.

NO SCHEMA CHANGE. Props are research data and machine.db is the production
database that the gates read; CLAUDE.md says ask before altering it and there
is nothing here that needs to. Everything lands in files:

    data/props/raw/<slot>/<event_id>.json.gz   the untouched response
    data/props/progress.jsonl                  one line per completed request
    data/props_nfl.parquet                     the materialised prices

Credits are logged to the existing api_usage table, which is an insert into a
table that already exists rather than a migration.

RESUMABLE, because 1,190 requests over a metered API will be interrupted.
progress.jsonl is the record; anything already in it is skipped. Raw responses
are kept so every downstream number can be re-derived without paying twice -
the same reason archive/ is append-only.

THE KEY IS READ FROM THE PERSISTED USER ENVIRONMENT on Windows, not from what
this process inherited. A Claude Code session in this project inherited a stale
ODDS_API_KEY - the free 500/month one - and spent four credits against the
wrong account before anyone noticed.
"""
import argparse
import datetime as dt
import gzip
import json
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import paths
from db import connect, utc_now
from ingest import http
from research.b3.b3_dryrun import schedule, snapshot_plan, MARKETS, SPORT, API
from research.b3.check_thursday import _key
from props.nfl_teams import to_name, to_abbr
from research.b3.preflight import BOOKS

OUT_DIR = paths.DATA_DIR / "props"
RAW_DIR = OUT_DIR / "raw"
PROGRESS = OUT_DIR / "progress.jsonl"
PARQUET = paths.DATA_DIR / "props_nfl.parquet"

CREDITS_PER_EVENT = 10 * len(MARKETS)
MATCH_WINDOW_MIN = 180        # same tolerance feeds.py uses for game matching
PAUSE = 0.05                  # gentle on a paid API; 1,190 requests


# ------------------------------------------------------------- progress ----

def load_progress() -> dict:
    done = {}
    if not PROGRESS.exists():
        return done
    with open(PROGRESS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            done[r["key"]] = r
    return done


def note(rec: dict) -> None:
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    with open(PROGRESS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def log_usage(endpoint: str, resp) -> None:
    """Into the EXISTING api_usage table. An insert, not a migration."""
    try:
        con = connect()
        con.execute(
            "INSERT INTO api_usage (ts, sport, endpoint, remaining, used)"
            " VALUES (?,?,?,?,?)",
            (utc_now(), "nfl", endpoint,
             int(resp.headers.get("x-requests-remaining") or 0),
             int(resp.headers.get("x-requests-used") or 0)))
        con.commit()
        con.close()
    except Exception:
        pass          # accounting must never lose a paid response


def save_raw(slot: str, event_id: str, payload) -> None:
    d = RAW_DIR / slot.replace(":", "").replace(" ", "_")
    d.mkdir(parents=True, exist_ok=True)
    with gzip.open(d / f"{event_id}.json.gz", "wt", encoding="utf-8") as f:
        json.dump(payload, f)


# ---------------------------------------------------------------- fetch ----

def slot_iso(slot: pd.Timestamp) -> str:
    return pd.Timestamp(slot).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_events(iso: str, key: str, spent: dict):
    r = http.get(f"{API}/historical/sports/{SPORT}/events",
                 params={"apiKey": key, "date": iso}, label="hist events")
    spent["credits"] += int(r.headers.get("x-requests-last") or 0)
    spent["remaining"] = int(r.headers.get("x-requests-remaining") or 0)
    log_usage("historical/events", r)
    body = r.json()
    return body.get("data", body)


def fetch_odds(event_id: str, iso: str, key: str, spent: dict):
    r = http.get(f"{API}/historical/sports/{SPORT}/events/{event_id}/odds",
                 params={"apiKey": key, "date": iso, "bookmakers": BOOKS,
                         "markets": ",".join(MARKETS),
                         "oddsFormat": "american"}, label="hist props")
    cost = int(r.headers.get("x-requests-last") or 0)
    spent["credits"] += cost
    spent["remaining"] = int(r.headers.get("x-requests-remaining") or 0)
    log_usage("historical/event-odds", r)
    return r.json(), cost


def match_event(game, events: list):
    """Our scheduled game -> the API's event. Matched, never guessed.

    On home-team NAME and kickoff time together, inside the same window
    feeds.py uses. Name alone is not enough - a team plays the same opponent
    twice a season - and time alone is not enough either, because six games
    kick off at once.
    """
    want_home = to_name(game.home_team)
    want_away = to_name(game.away_team)
    if want_home is None or want_away is None:
        return None, "unmapped team abbreviation"
    kick = pd.Timestamp(game.kickoff_utc)
    best, best_gap = None, None
    for e in events:
        if e.get("home_team") != want_home or e.get("away_team") != want_away:
            continue
        try:
            ct = pd.Timestamp(e["commence_time"])
        except Exception:
            continue
        gap = abs((ct - kick).total_seconds()) / 60.0
        if gap <= MATCH_WINDOW_MIN and (best_gap is None or gap < best_gap):
            best, best_gap = e, gap
    if best is None:
        return None, "no event with both teams inside the window"
    return best, f"{best_gap:.0f} min"


# --------------------------------------------------------------- driver ----

def run(execute: bool, max_credits: int, limit_games: int = 0) -> int:
    """--limit-games narrows the PLAN, which is what makes a smoke test possible.

    The budget guard below refuses to start when the whole plan costs more than
    --max-credits, which is right for the real run and useless for a trial. So
    a trial narrows the plan deliberately rather than relying on the cap to
    stop it partway: the Phase 5 backfill's ten-request test found a bug worth
    88 games, and it only found it because the small run was a real run.
    """
    key = _key() if execute else ""
    s = schedule()
    plan = snapshot_plan(s)
    if limit_games:
        # Spread across seasons rather than taking the first N, which would all
        # come from one week of 2023 and prove nothing about the other two.
        plan = (plan.groupby("season", group_keys=False)
                    .apply(lambda g: g.head(max(1, limit_games // 3)))
                    .reset_index(drop=True))
        print(f"LIMITED to {len(plan)} games for a trial run\n")
    plan = plan.merge(s[["game_id", "home_team", "away_team"]]
                      .drop_duplicates(), on="game_id", how="left",
                      suffixes=("", "_y"))
    done = load_progress()
    slots = sorted(plan["slot"].unique())

    todo = [g for g in plan.itertuples()
            if f"odds:{g.game_id}" not in done]
    print("=" * 74)
    print("B3  NFL receiving props, closing snapshots, 2023-2025")
    print("=" * 74)
    print(f"\n{len(plan):,} games over {len(slots)} slots")
    print(f"{len(done):,} requests already recorded, {len(todo):,} games to go")
    est = len(todo) * CREDITS_PER_EVENT + len(slots)
    print(f"estimated cost from here: ~{est:,} credits")
    if not execute:
        print("\nDRY RUN - nothing fetched. Add --execute to spend.")
        return 0
    if est > max_credits:
        print(f"\nThat exceeds --max-credits {max_credits:,}. Raise the cap "
              f"deliberately or narrow the plan; refusing to start.")
        return 1

    spent = {"credits": 0, "remaining": None}
    tally = {"events": 0, "odds": 0, "unmatched": 0, "empty": 0, "rows": 0}
    unmatched = []
    t0 = time.time()

    for i, slot in enumerate(slots, 1):
        iso = slot_iso(slot)
        games = plan[plan["slot"] == slot]
        pending = [g for g in games.itertuples()
                   if f"odds:{g.game_id}" not in done]
        if not pending:
            continue

        try:
            events = fetch_events(iso, key, spent)
            tally["events"] += 1
        except Exception as e:
            print(f"  [{i}/{len(slots)}] {iso} events FAILED: "
                  f"{http.redact(e)}")
            note({"key": f"events:{iso}", "ok": False,
                  "err": http.redact(str(e))[:200], "at": utc_now()})
            continue

        for g in pending:
            if spent["credits"] + CREDITS_PER_EVENT > max_credits:
                print(f"\nstopping: next request would pass the "
                      f"{max_credits:,} cap")
                _finish(spent, tally, unmatched, t0)
                return 0
            ev, why = match_event(g, events)
            if ev is None:
                tally["unmatched"] += 1
                unmatched.append((g.game_id, why))
                note({"key": f"odds:{g.game_id}", "ok": False,
                      "reason": f"unmatched: {why}", "at": utc_now()})
                continue
            try:
                payload, cost = fetch_odds(ev["id"], iso, key, spent)
            except Exception as e:
                print(f"    {g.game_id} odds FAILED: {http.redact(e)}")
                note({"key": f"odds:{g.game_id}", "ok": False,
                      "err": http.redact(str(e))[:200], "at": utc_now()})
                continue
            data = payload.get("data", {})
            nb = len(data.get("bookmakers", []))
            save_raw(iso, ev["id"], payload)
            tally["odds"] += 1
            if nb == 0:
                tally["empty"] += 1
            note({"key": f"odds:{g.game_id}", "ok": True, "slot": iso,
                  "event_id": ev["id"], "books": nb, "cost": cost,
                  "match": why, "at": utc_now()})
            time.sleep(PAUSE)

        if i % 20 == 0 or i == len(slots):
            rate = spent["credits"] / max(time.time() - t0, 1) * 60
            print(f"  [{i:>3}/{len(slots)}] {iso}  "
                  f"games {tally['odds']:>4}  spent {spent['credits']:>6,}  "
                  f"remaining {spent['remaining']:,}  "
                  f"({rate:.0f} credits/min)")

    _finish(spent, tally, unmatched, t0)
    return 0


def _finish(spent, tally, unmatched, t0):
    print("\n" + "=" * 74)
    print("DONE")
    print("=" * 74)
    print(f"  events-list calls  {tally['events']:,}")
    print(f"  event-odds calls   {tally['odds']:,}")
    print(f"  empty responses    {tally['empty']:,}")
    print(f"  unmatched games    {tally['unmatched']:,}")
    print(f"  credits spent      {spent['credits']:,}")
    print(f"  credits remaining  {spent['remaining']:,}"
          if spent["remaining"] is not None else "")
    print(f"  elapsed            {(time.time() - t0) / 60:.1f} min")
    if unmatched:
        print("\n  unmatched (reported, never silently dropped):")
        for gid, why in unmatched[:20]:
            print(f"    {gid}  {why}")
        if len(unmatched) > 20:
            print(f"    ... and {len(unmatched) - 20} more")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--max-credits", type=int, default=17_000)
    ap.add_argument("--limit-games", type=int, default=0,
                    help="trial run: narrow the plan to roughly N games, "
                         "spread across seasons")
    a = ap.parse_args()
    raise SystemExit(run(a.execute, a.max_credits, a.limit_games))
