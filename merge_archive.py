"""Merge archive/ CSVs (from cloud runs) into the LOCAL master database.

Run on your laptop after pulling the repo:
  python merge_archive.py

Dedupe rules:
  odds_snapshots: skip rows whose (game_id, ts, book, snapshot_type)
                  already exist locally
  games:          upsert, but never overwrite a final score with a blank
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import connect

ARCHIVE = Path(__file__).parent / "archive"


def merge():
    if not ARCHIVE.exists():
        print("No archive/ folder yet — nothing to merge.")
        return
    con = connect()
    added_snaps = added_games = 0

    # Deduplication is the ux_snap_dedupe UNIQUE index, not a per-row SELECT.
    # The old version probed the table once per archived row; with no index
    # that was a full scan each time, so the merge was O(n^2) in the history
    # and reached 84 minutes after a year of collection. OR IGNORE pushes the
    # same rule into the database, where it costs one B-tree lookup.
    before = con.total_changes
    for p in sorted(ARCHIVE.glob("odds-*.csv")):
        with open(p, newline="", encoding="utf-8") as f:
            con.executemany(
                "INSERT OR IGNORE INTO odds_snapshots (game_id, sport, ts, book,"
                " away_ml, home_ml, snapshot_type, commence_time)"
                " VALUES (?,?,?,?,?,?,?,?)",
                [(row["game_id"], row["sport"], row["ts"], row["book"],
                  row["away_ml"] or None, row["home_ml"] or None,
                  row["snapshot_type"],
                  # absent from CSVs archived before commence_time was added
                  row.get("commence_time") or None)
                 for row in csv.DictReader(f)])
    added_snaps = con.total_changes - before

    for p in sorted(ARCHIVE.glob("games-*.csv")):
        with open(p, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                con.execute(
                    """INSERT INTO games (game_id, sport, game_date, away, home,
                         away_starter, home_starter, away_score, home_score, status)
                       VALUES (?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(game_id) DO UPDATE SET
                         away_starter=COALESCE(excluded.away_starter, away_starter),
                         home_starter=COALESCE(excluded.home_starter, home_starter),
                         away_score=COALESCE(excluded.away_score, away_score),
                         home_score=COALESCE(excluded.home_score, home_score),
                         status=CASE WHEN status='final' THEN status
                                     ELSE excluded.status END""",
                    (row["game_id"], row["sport"], row["game_date"], row["away"],
                     row["home"], row.get("away_starter") or None,
                     row.get("home_starter") or None,
                     row.get("away_score") or None, row.get("home_score") or None,
                     row.get("status") or "scheduled"))
                added_games += 1

    con.commit()
    total = con.execute("SELECT COUNT(*) c FROM odds_snapshots").fetchone()["c"]
    con.close()
    print(f"Merged: +{added_snaps} new snapshots, {added_games} game upserts. "
          f"Local archive now holds {total} snapshots.")


if __name__ == "__main__":
    merge()
