"""backfill.backfill_schedule on a fake API response and a temp database.

abstractGameState is "Final" for a postponed game, and the upsert assigned
scores unconditionally, so a re-run could store a postponement as a final
with no score, or wipe a real final score with NULL.
"""
import pytest

import backfill
import db


def _game(pk, detailed, away=None, home=None):
    return {"gamePk": pk,
            "status": {"abstractGameState": "Final", "detailedState": detailed},
            "teams": {"away": {"team": {"name": "Toronto Blue Jays"}, "score": away},
                      "home": {"team": {"name": "Baltimore Orioles"}, "score": home}}}


class _Resp:
    def __init__(self, games):
        self._j = {"dates": [{"date": "2026-05-23", "games": games}]}

    def raise_for_status(self):
        pass

    def json(self):
        return self._j


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "machine.db")
    db.init()
    c = db.connect()
    yield c
    c.close()


def test_a_postponed_game_is_not_stored_as_final(con, monkeypatch):
    monkeypatch.setattr(backfill.requests, "get",
                        lambda *a, **k: _Resp([_game(823543, "Postponed")]))
    backfill.backfill_schedule(2026)
    assert con.execute("SELECT 1 FROM games WHERE game_id='mlb-823543'").fetchone() is None


def test_a_final_score_is_never_blanked(con, monkeypatch):
    con.execute("INSERT INTO games (game_id, sport, game_date, away, home,"
                " away_score, home_score, status) VALUES ('mlb-1','mlb',"
                "'2026-05-23','Toronto Blue Jays','Baltimore Orioles',0,2,'final')")
    con.commit()
    monkeypatch.setattr(backfill.requests, "get",
                        lambda *a, **k: _Resp([_game(1, "Final", 7, None)]))
    backfill.backfill_schedule(2026)
    r = con.execute("SELECT away_score, home_score FROM games WHERE game_id='mlb-1'").fetchone()
    assert (r[0], r[1]) == (0, 2)
