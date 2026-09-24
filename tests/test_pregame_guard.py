"""A paper bet must be one somebody could actually have placed - through the
real bets.paper.place() on a temp database, so removing the guard fails here.
"""
import datetime as dt

import pytest

import db
from bets import paper

NOW = dt.datetime.now(dt.timezone.utc)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "machine.db")
    # Guardrails read Statcast for the opener check; there is none here.
    monkeypatch.setattr(paper, "flags_for_game", lambda con, gid, side: {})
    db.init()
    con = db.connect()
    n = [0]

    def game(name, start, status, snap_offset_min=-360):
        n[0] += 1
        sid, oid = f"mlb-90000{n[0]}", f"mlb-odds{name}"
        con.execute("INSERT INTO games (game_id, sport, game_date, start_time_utc,"
                    " away, home, status) VALUES (?,?,?,?,?,?,?)",
                    (sid, "mlb", str(NOW.date()),
                     start.isoformat() if start else None, f"A{name}", f"H{name}",
                     status))
        # 0.78 against a no-vig 0.61 clears the edge threshold, so the guard
        # is the only thing that can stop a bet here.
        con.execute("INSERT INTO predictions (game_id, sport, created_at,"
                    " model_version, proj_margin, home_win_prob)"
                    " VALUES (?,?,?,?,?,?)",
                    (sid, "mlb", db.utc_now(), "v1", 1.5, 0.78))
        if start:
            con.execute("INSERT INTO games (game_id, sport, game_date,"
                        " start_time_utc, away, home) VALUES (?,?,?,?,?,?)",
                        (oid, "mlb", str(NOW.date()), start.isoformat(),
                         f"A{name}", f"H{name}"))
            con.execute("INSERT INTO odds_snapshots (game_id, sport, ts, book,"
                        " away_ml, home_ml, snapshot_type, commence_time)"
                        " VALUES (?,?,?,?,?,?,?,?)",
                        (oid, "mlb",
                         (NOW + dt.timedelta(minutes=snap_offset_min)).isoformat(),
                         "fanduel", 150, -170, "open", start.isoformat()))
        con.commit()
        return sid

    yield game
    con.close()


def _placed(sid):
    con = db.connect()
    r = con.execute("SELECT COUNT(*) FROM bets WHERE game_id=? AND mode='paper'",
                    (sid,)).fetchone()[0]
    con.close()
    return r


def test_a_game_safely_ahead_is_placed(world):
    sid = world("ahead", NOW + dt.timedelta(hours=4), "scheduled")
    paper.place(str(NOW.date()))
    assert _placed(sid) == 1


def test_a_game_already_under_way_is_not(world):
    sid = world("started", NOW - dt.timedelta(hours=2), "live")
    paper.place(str(NOW.date()))
    assert _placed(sid) == 0


def test_a_game_inside_the_cutoff_is_not(world):
    sid = world("imminent", NOW + dt.timedelta(minutes=paper.PLACE_CUTOFF_MIN - 1),
                "scheduled")
    paper.place(str(NOW.date()))
    assert _placed(sid) == 0


def test_a_price_taken_after_now_is_not_a_bet(world):
    sid = world("future", NOW + dt.timedelta(hours=4), "scheduled",
                snap_offset_min=+30)
    paper.place(str(NOW.date()))
    assert _placed(sid) == 0


def test_a_game_with_no_known_start_is_refused(world):
    sid = world("nostart", None, "scheduled")
    paper.place(str(NOW.date()))
    assert _placed(sid) == 0
