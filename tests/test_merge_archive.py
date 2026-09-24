"""merge_archive against a throwaway database: the real merge SQL, real CSVs.

The merge used to drop start_time_utc, so every odds row that reached the
laptop through archive/ had no start time and feeds.odds_twin could never
match it. Paper betting stopped on 2026-09-24 because of it.
"""
import csv

import pytest

import db
import merge_archive

HEADER = ["game_id", "sport", "game_date", "start_time_utc", "venue_id", "away",
          "home", "away_starter", "home_starter", "away_score", "home_score",
          "status", "away_starter_id", "home_starter_id"]


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "machine.db")
    arch = tmp_path / "archive"
    arch.mkdir()
    monkeypatch.setattr(merge_archive, "ARCHIVE", arch)
    db.init()

    def write(name, rows):
        with open(arch / name, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=HEADER)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in HEADER})

    return write


def _game(**kw):
    base = {"game_id": "mlb-abc123def", "sport": "mlb", "game_date": "2026-09-24",
            "away": "Tampa Bay Rays", "home": "New York Yankees",
            "status": "scheduled"}
    base.update(kw)
    return base


def _row(game_id):
    con = db.connect()
    r = con.execute("SELECT * FROM games WHERE game_id=?", (game_id,)).fetchone()
    con.close()
    return r


def test_the_start_time_survives_the_merge(world):
    world("games-2026-09-24-0141.csv",
          [_game(start_time_utc="2026-09-24T23:06:00Z", venue_id="3313")])
    merge_archive.merge()
    r = _row("mlb-abc123def")
    assert r["start_time_utc"] == "2026-09-24T23:06:00Z"
    assert r["venue_id"] == "3313"


def test_a_later_file_without_a_start_time_does_not_blank_it(world):
    world("games-2026-09-24-0141.csv", [_game(start_time_utc="2026-09-24T23:06:00Z")])
    world("games-2026-09-24-1400.csv", [_game()])
    merge_archive.merge()
    assert _row("mlb-abc123def")["start_time_utc"] == "2026-09-24T23:06:00Z"


def test_a_final_score_is_never_overwritten(world):
    world("games-2026-09-24-0141.csv",
          [_game(game_id="mlb-824060", status="final", away_score="4",
                 home_score="5", start_time_utc="2026-09-23T23:40:00Z")])
    world("games-2026-09-24-1400.csv",
          [_game(game_id="mlb-824060", status="scheduled",
                 start_time_utc="2026-09-24T23:40:00Z")])
    merge_archive.merge()
    r = _row("mlb-824060")
    assert (r["status"], r["away_score"], r["home_score"]) == ("final", 4, 5)
    assert r["start_time_utc"] == "2026-09-23T23:40:00Z"


def test_an_impossible_final_is_rejected_not_merged(world):
    world("games-2026-09-23-0203.csv",
          [_game(game_id="mlb-espn-401817035", status="final", away_score="0",
                 home_score="0")])
    merge_archive.merge()
    assert _row("mlb-espn-401817035") is None


def test_a_utc_dated_row_is_re_dated_from_its_start_time(world):
    # 8:20pm ET on the 27th is 00:20 UTC on the 28th.
    world("games-2026-09-23-1823.csv",
          [_game(game_id="nfl-espn-401872962", sport="nfl", game_date="2026-09-28",
                 away="Los Angeles Rams", home="Denver Broncos",
                 start_time_utc="2026-09-28T00:20Z")])
    merge_archive.merge()
    assert _row("nfl-espn-401872962")["game_date"] == "2026-09-27"


def test_files_merged_by_the_old_version_are_read_once_more(world):
    world("games-2026-09-24-0141.csv", [_game(start_time_utc="2026-09-24T23:06:00Z")])
    con = db.connect()
    p = merge_archive.ARCHIVE / "games-2026-09-24-0141.csv"
    con.execute("INSERT INTO games (game_id, sport, game_date, away, home)"
                " VALUES ('mlb-abc123def','mlb','2026-09-24','Tampa Bay Rays',"
                "'New York Yankees')")
    con.execute("INSERT INTO merged_files VALUES (?,?,?,?)",
                (p.name, p.stat().st_size, 1, "2026-09-24T04:32:20+00:00"))
    con.commit()
    con.close()
    merge_archive.merge()
    assert _row("mlb-abc123def")["start_time_utc"] == "2026-09-24T23:06:00Z"
