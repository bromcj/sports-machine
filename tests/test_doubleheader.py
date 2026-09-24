"""feeds.odds_twin on doubleheaders, against a throwaway database.

The Stats API lists a straight doubleheader's game 2 at game 1's time plus
five minutes, so both games used to find game 1's odds row - 23 odds rows in
market_close were claimed twice. A doubleheader inside the match window is
now refused, as the docs always said; a split one still matches by time.
"""
import pytest

import db
import feeds


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "machine.db")
    db.init()
    c = db.connect()
    yield c
    c.close()


def _game(con, gid, start, odds=False):
    con.execute("INSERT INTO games (game_id, sport, game_date, start_time_utc,"
                " away, home) VALUES (?,?,?,?,?,?)",
                (gid, "mlb", "2026-04-05", start, "Chicago Cubs",
                 "Cleveland Guardians"))
    if odds:
        con.execute("INSERT INTO odds_snapshots (game_id, sport, ts, book, away_ml,"
                    " home_ml, snapshot_type, commence_time) VALUES"
                    " (?,?,?,?,?,?,?,?)", (gid, "mlb", "2026-04-05T12:00:00+00:00",
                                            "pinnacle", 120, -130, "open", start))


def test_a_straight_doubleheader_is_refused_not_given_game_1s_odds(con):
    _game(con, "mlb-824459", "2026-04-05T17:10:00Z")
    _game(con, "mlb-824460", "2026-04-05T17:15:00Z")
    _game(con, "mlb-29fe7472aaaa", "2026-04-05T17:10:00Z", odds=True)
    _game(con, "mlb-b58a8d03bbbb", "2026-04-05T20:42:49Z", odds=True)
    for gid in ("mlb-824459", "mlb-824460"):
        twin, note = feeds.odds_twin(con, gid)
        assert twin is None and "doubleheader" in note


def test_a_split_doubleheader_matches_each_game_by_time(con):
    _game(con, "mlb-900001", "2026-04-05T17:10:00Z")
    _game(con, "mlb-900002", "2026-04-05T23:10:00Z")
    _game(con, "mlb-aaaa1111", "2026-04-05T17:11:00Z", odds=True)
    _game(con, "mlb-bbbb2222", "2026-04-05T23:11:00Z", odds=True)
    assert feeds.odds_twin(con, "mlb-900001")[0] == "mlb-aaaa1111"
    assert feeds.odds_twin(con, "mlb-900002")[0] == "mlb-bbbb2222"
