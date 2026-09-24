"""Merge archive/ CSVs (from cloud runs) into the LOCAL master database.

Run on your laptop after pulling the repo:
  python merge_archive.py            only files not merged before
  python merge_archive.py --all      re-read every file (rebuild / repair)

Two independent safety nets, because this runs unattended behind
machine_daily.bat and a silent mis-merge is worse than a loud failure:

  watermark   merged_files records what has already been read, so a daily
              merge costs one pass over the new files instead of over the
              whole history. Without it this re-read every CSV ever archived.
  dedupe      the ux_snap_dedupe UNIQUE index. Belt and braces: --all, a
              restored backup, or a half-finished run re-reads a file, and
              the rows still cannot land twice.

Games are upserted, never blanked: 'final' is terminal and a NULL never
overwrites a real value. Every column the export carries is merged; a column
dropped here is a column the laptop never has. start_time_utc is the one that
matters most: feeds.odds_twin matches on it, so an odds row without it can
never be matched to its game.
"""
import csv
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import as_utc, connect, normalize_game_dates
from ingest import quality

ARCHIVE = Path(__file__).parent / "archive"

# Files merged before this instant were read by a version that dropped
# start_time_utc, venue_id, the starter ids and market_last_update. They are
# read once more, so the rows they already created get those columns; the
# re-read stamps a new merged_at and they are not read again.
FULL_COLUMNS_SINCE = "2026-09-24T05:00:00+00:00"

SNAP_SQL = ("INSERT OR IGNORE INTO odds_snapshots (game_id, sport, ts, book,"
            " away_ml, home_ml, snapshot_type, commence_time,"
            " market_last_update) VALUES (?,?,?,?,?,?,?,?,?)")

GAME_SQL = """INSERT INTO games (game_id, sport, game_date, start_time_utc,
                venue_id, away, home, away_starter, home_starter,
                away_starter_id, home_starter_id, away_score, home_score, status)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(game_id) DO UPDATE SET
                start_time_utc=CASE WHEN games.status='final'
                                    THEN COALESCE(games.start_time_utc,
                                                  excluded.start_time_utc)
                                    ELSE COALESCE(excluded.start_time_utc,
                                                  games.start_time_utc) END,
                venue_id=COALESCE(excluded.venue_id, games.venue_id),
                away_starter=COALESCE(excluded.away_starter, games.away_starter),
                home_starter=COALESCE(excluded.home_starter, games.home_starter),
                away_starter_id=COALESCE(excluded.away_starter_id,
                                         games.away_starter_id),
                home_starter_id=COALESCE(excluded.home_starter_id,
                                         games.home_starter_id),
                away_score=CASE WHEN games.status='final' THEN games.away_score
                           ELSE COALESCE(excluded.away_score, games.away_score) END,
                home_score=CASE WHEN games.status='final' THEN games.home_score
                           ELSE COALESCE(excluded.home_score, games.home_score) END,
                status=CASE WHEN games.status='final' THEN 'final'
                            ELSE excluded.status END"""


def _snapshots(f):
    # Archived before utc_now(), ts may be naive. Normalise on the way
    # in so a fresh checkout rebuilding from archive/ agrees with a DB
    # that has been running all along.
    return [(r["game_id"], r["sport"], as_utc(r["ts"]), r["book"],
             r["away_ml"] or None, r["home_ml"] or None, r["snapshot_type"],
             # absent from CSVs archived before commence_time was added
             r.get("commence_time") or None,
             r.get("market_last_update") or None)
            for r in csv.DictReader(f)]


def _games(f, bad):
    """Game rows, through the same quality rules ingest applies.

    Old CSVs predate those rules - four of them hold TOR @ BAL as a 0-0
    final - and a merge that skipped the check would bring such a row back on
    every `--all`, where 'final' then makes it permanent.
    """
    out = []
    for r in csv.DictReader(f):
        status = r.get("status") or "scheduled"
        away_score = r.get("away_score") or None
        home_score = r.get("home_score") or None
        if not bad.check(quality.game(r["away"], r["home"], r["game_date"],
                                      away_score, home_score, status,
                                      r["sport"])):
            continue
        out.append((r["game_id"], r["sport"], r["game_date"],
                    r.get("start_time_utc") or None, r.get("venue_id") or None,
                    r["away"], r["home"],
                    r.get("away_starter") or None, r.get("home_starter") or None,
                    r.get("away_starter_id") or None,
                    r.get("home_starter_id") or None,
                    away_score, home_score, status))
    return out


def merge(rebuild: bool = False):
    if not ARCHIVE.exists():
        print("No archive/ folder yet — nothing to merge.")
        return
    con = connect()

    # Keyed on (name, bytes), not name alone. Archive files are immutable in
    # practice - each carries its own UTC timestamp in the name - but if one is
    # ever rewritten, or arrives truncated from a half-finished git pull, its
    # size changes and it gets read again rather than trusted.
    seen = {} if rebuild else {
        r["name"]: r["bytes"]
        for r in con.execute("SELECT name, bytes FROM merged_files"
                             " WHERE merged_at >= ?", (FULL_COLUMNS_SINCE,))}

    files = sorted(ARCHIVE.glob("odds-*.csv")) + sorted(ARCHIVE.glob("games-*.csv"))
    todo = [p for p in files if seen.get(p.name) != p.stat().st_size]
    if not todo:
        con.close()
        print(f"Nothing new — all {len(files)} archive files already merged. "
              f"(--all to re-read them.)")
        return

    added_snaps = added_games = 0
    bad = quality.Rejects("merge")
    for p in todo:
        before = con.total_changes
        with open(p, newline="", encoding="utf-8") as f:
            if p.name.startswith("odds-"):
                rows = _snapshots(f)
                con.executemany(SNAP_SQL, rows)
                added_snaps += con.total_changes - before
            else:
                rows = _games(f, bad)
                con.executemany(GAME_SQL, rows)
                added_games += con.total_changes - before
        con.execute(
            "INSERT OR REPLACE INTO merged_files (name, bytes, rows, merged_at)"
            " VALUES (?,?,?,?)",
            (p.name, p.stat().st_size, len(rows),
             dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")))
        # Commit per file, so an interrupted merge keeps whatever it finished.
        # A file cut off mid-write is simply never marked, and the next run
        # reads it again - which the UNIQUE index makes harmless.
        con.commit()

    # A start time that just arrived fixes the game's local date too. The
    # upsert does not rewrite game_date itself, because an old CSV's date may
    # be a UTC one; the date derived from the start time is the right one.
    redated = normalize_game_dates(con)
    con.commit()
    total = con.execute("SELECT COUNT(*) c FROM odds_snapshots").fetchone()["c"]
    con.close()
    print(f"Merged {len(todo)} new file(s) of {len(files)}: "
          f"+{added_snaps} snapshots, {added_games} game upserts"
          + (f", {redated} re-dated" if redated else "") + ". "
          f"Local archive now holds {total} snapshots.")
    bad.report()


if __name__ == "__main__":
    merge(rebuild="--all" in sys.argv)
