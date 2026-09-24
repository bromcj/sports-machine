"""bets.log.closing_snapshot must never use a price taken after first pitch.

The odds API keeps serving a market once a game starts, switching to in-play
prices (a real pull returned Giants -10000). Deleting the in-play filter was
caught only by the golden test, which does not run in CI.
"""
import pytest

import db
from bets import log


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "machine.db")
    db.init()
    c = db.connect()
    for ts, a, h in (("2026-09-23T22:55:00+00:00", 120, -130),     # 10 min before
                     ("2026-09-23T23:10:00+00:00", 5000, -10000)):  # in play
        c.execute("INSERT INTO odds_snapshots (game_id, sport, ts, book, away_ml,"
                  " home_ml, snapshot_type, commence_time) VALUES (?,?,?,?,?,?,?,?)",
                  ("mlb-oddsrow", "mlb", ts, "pinnacle", a, h, "close",
                   "2026-09-23T23:05:00Z"))
    c.commit()
    yield c
    c.close()


def test_the_close_is_the_last_pregame_price(con):
    snap = log.closing_snapshot("mlb-oddsrow")
    assert snap["away_ml"] == 120
    assert snap["minutes_before_start"] == 10.0 and snap["is_closing"]
