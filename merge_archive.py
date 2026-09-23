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
overwrites a real value.
"""
import csv
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import connect

ARCHIVE = Path(__file__).parent / "archive"

SNAP_SQL = ("INSERT OR IGNORE INTO odds_snapshots (game_id, sport, ts, book,"
            " away_ml, home_ml, snapshot_type, commence_time)"
            " VALUES (?,?,?,?,?,?,?,?)")

GAME_SQL = """INSERT INTO games (game_id, sport, game_date, away, home,
                away_starter, home_starter, away_score, home_score, status)
              VALUES (?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(game_id) DO UPDATE SET
                away_starter=COALESCE(excluded.away_starter, away_starter),
                home_starter=COALESCE(excluded.home_starter, home_starter),
                away_score=CASE WHEN status='final' THEN away_score
                           ELSE COALESCE(excluded.away_score, away_score) END,
                home_score=CASE WHEN status='final' THEN home_score
                           ELSE COALESCE(excluded.home_score, home_score) END,
                status=CASE WHEN status='final' THEN 'final'
                            ELSE excluded.status END"""


def _snapshots(f):
    return [(r["game_id"], r["sport"], r["ts"], r["book"],
             r["away_ml"] or None, r["home_ml"] or None, r["snapshot_type"],
             # absent from CSVs archived before commence_time was added
             r.get("commence_time") or None)
            for r in csv.DictReader(f)]


def _games(f):
    return [(r["game_id"], r["sport"], r["game_date"], r["away"], r["home"],
             r.get("away_starter") or None, r.get("home_starter") or None,
             r.get("away_score") or None, r.get("home_score") or None,
             r.get("status") or "scheduled")
            for r in csv.DictReader(f)]


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
        for r in con.execute("SELECT name, bytes FROM merged_files")}

    files = sorted(ARCHIVE.glob("odds-*.csv")) + sorted(ARCHIVE.glob("games-*.csv"))
    todo = [p for p in files if seen.get(p.name) != p.stat().st_size]
    if not todo:
        con.close()
        print(f"Nothing new — all {len(files)} archive files already merged. "
              f"(--all to re-read them.)")
        return

    added_snaps = added_games = 0
    for p in todo:
        before = con.total_changes
        with open(p, newline="", encoding="utf-8") as f:
            if p.name.startswith("odds-"):
                rows = _snapshots(f)
                con.executemany(SNAP_SQL, rows)
                added_snaps += con.total_changes - before
            else:
                rows = _games(f)
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

    total = con.execute("SELECT COUNT(*) c FROM odds_snapshots").fetchone()["c"]
    con.close()
    print(f"Merged {len(todo)} new file(s) of {len(files)}: "
          f"+{added_snaps} snapshots, {added_games} game upserts. "
          f"Local archive now holds {total} snapshots.")


if __name__ == "__main__":
    merge(rebuild="--all" in sys.argv)
