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

    for p in sorted(ARCHIVE.glob("odds-*.csv")):
        with open(p, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                dup = con.execute(
                    "SELECT 1 FROM odds_snapshots WHERE game_id=? AND ts=? "
                    "AND book=? AND snapshot_type=?",
                    (row["game_id"], row["ts"], row["book"],
                     row["snapshot_type"])).fetchone()
                if dup:
                    continue
                con.execute(
                    "INSERT INTO odds_snapshots (game_id, sport, ts, book, "
                    "away_ml, home_ml, snapshot_type) VALUES (?,?,?,?,?,?,?)",
                    (row["game_id"], row["sport"], row["ts"], row["book"],
                     row["away_ml"] or None, row["home_ml"] or None,
                     row["snapshot_type"]))
                added_snaps += 1

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
