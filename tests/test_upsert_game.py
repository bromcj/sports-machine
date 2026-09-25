"""ingest.odds.upsert_game: the odds feed's games row, shared by the cloud's
pulls and the scanner's sportsbook adapter. Finished is final (CLAUDE.md): a
re-reported start never moves a final game's date, and nothing here touches
its status, score or teams. Each test gets its own temp database."""
import pytest

import db
from ingest.odds import upsert_game
from ingest.quality import Rejects
from scanner.venues import sportsbook

AWAY, HOME = "Atlanta Falcons", "Green Bay Packers"


@pytest.fixture
def con(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init()
    capsys.readouterr()
    c = db.connect()
    yield c
    c.close()


def _seed(con, gid, status, away_score=None, home_score=None):
    con.execute("INSERT INTO games (game_id, sport, game_date, start_time_utc,"
                " away, home, away_score, home_score, status)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (gid, "nfl", "2026-09-20", "2026-09-20T17:00:00+00:00",
                 AWAY, HOME, away_score, home_score, status))


def _game(con, gid):
    return tuple(con.execute(
        "SELECT game_date, status, away_score, home_score, away, home"
        " FROM games WHERE game_id=?", (gid,)).fetchone())


def test_a_final_games_date_status_score_and_teams_never_move(con):
    _seed(con, "nfl-fin", "final", 17, 24)
    assert upsert_game(con, "nfl-fin", "nfl", "2026-09-22T17:00:00Z",
                       "Atlanta Falcons FC", HOME, Rejects())
    assert _game(con, "nfl-fin") == ("2026-09-20", "final", 17, 24, AWAY, HOME)


def test_the_scanner_path_keeps_a_final_game_too(con):
    # sportsbook.write re-reports the same games row every poll.
    _seed(con, "nfl-e5fb", "final", 10, 3)
    ev = {"id": "e5fb", "commence_time": "2026-09-28T00:20:00Z",
          "home_team": HOME, "away_team": AWAY, "bookmakers": [
              {"key": "pinnacle", "markets": [{"key": "h2h", "outcomes": [
                  {"name": AWAY, "price": 205}, {"name": HOME, "price": -230}]}]}]}
    assert sportsbook.write(con, "nfl", [ev], "2026-09-27T15:00:00+00:00")["games"] == 1
    assert _game(con, "nfl-e5fb") == ("2026-09-20", "final", 10, 3, AWAY, HOME)


def test_an_unfinished_games_date_still_follows_the_feed(con):
    _seed(con, "nfl-sch", "scheduled")
    # 00:20 UTC on the 28th is 8:20pm ET on the 27th: game_date is the ET date.
    assert upsert_game(con, "nfl-sch", "nfl", "2026-09-28T00:20:00Z", AWAY, HOME,
                       Rejects())
    assert _game(con, "nfl-sch") == ("2026-09-27", "scheduled", None, None, AWAY, HOME)
    assert con.execute("SELECT start_time_utc FROM games WHERE game_id='nfl-sch'"
                       ).fetchone()[0] == "2026-09-28T00:20:00Z"
