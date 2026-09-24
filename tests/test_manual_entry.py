"""bets/manual.py against a throwaway database: the real entry and grading.

On live data the documented command was refused for every game (each game is
stored once per feed), no moneyline bet could ever settle, 'GB' was read as
the away team, and spreads/totals were graded against the moneyline.
"""
import pytest

import db
from bets import manual


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "machine.db")
    db.init()
    c = db.connect()
    rows = [  # one real game, three feeds
        ("nfl-espn-401872948", "2099-09-24T00:15Z", None),
        ("nfl-e5fbd3b3953b896b", "2099-09-24T00:15:00Z", (216, -254)),
    ]
    for gid, start, ml in rows:
        c.execute("INSERT INTO games (game_id, sport, game_date, start_time_utc,"
                  " away, home) VALUES (?,?,?,?,?,?)",
                  (gid, "nfl", "2099-09-23", start, "Atlanta Falcons",
                   "Green Bay Packers"))
        if ml:
            for book, a, h in (("pinnacle", *ml), ("draftkings", 205, -250)):
                c.execute("INSERT INTO odds_snapshots (game_id, sport, ts, book,"
                          " away_ml, home_ml, snapshot_type, commence_time)"
                          " VALUES (?,?,?,?,?,?,?,?)",
                          (gid, "nfl", "2099-09-23T01:41:02+00:00", book, a, h,
                           "close", start))
    c.commit()
    yield c
    c.close()


def test_a_matchup_string_resolves_to_the_score_feed_row(con):
    g = manual.resolve_game(con, "nfl", "2099-09-23", "Falcons at Packers")
    assert g["game_id"] == "nfl-espn-401872948"


def test_entry_records_the_fair_and_best_price_through_the_odds_twin(con):
    bid = manual.enter("nfl", "2099-09-23", "Falcons at Packers", "h2h",
                       "Packers", -200, "draftkings", 10, tag="research")
    r = con.execute("SELECT best_available, p_fair_at_bet, fair_source FROM bets"
                    " WHERE bet_id=?", (bid,)).fetchone()
    # Pinnacle -254 / +216 de-vigged; longest home price is -250.
    assert r["best_available"] == -250
    assert r["p_fair_at_bet"] == pytest.approx(0.69394, abs=1e-4)
    assert r["fair_source"] == "pinnacle"


@pytest.mark.parametrize("side,want", [("Packers", "home"), ("Green Bay Packers", "home"),
                                       ("atlanta falcons", "away"), ("home", "home"),
                                       ("GB", None), ("Bay", "home"), ("Atlanta Packers", None)])
def test_a_side_must_name_exactly_one_team(side, want):
    g = {"away": "Atlanta Falcons", "home": "Green Bay Packers"}
    assert manual.team_side(g, side) == want


def test_an_unclear_side_is_refused_at_entry(con):
    with pytest.raises(manual.EntryError):
        manual.enter("nfl", "2099-09-23", "Falcons at Packers", "h2h",
                     "GB", -200, "draftkings", 10)


def test_a_total_is_not_given_moneyline_shop_or_info(con):
    bid = manual.enter("nfl", "2099-09-23", "Falcons at Packers", "totals",
                       "over", -110, "draftkings", 10, prop_line=44.5)
    r = con.execute("SELECT p_fair_at_bet, best_available FROM bets WHERE bet_id=?",
                    (bid,)).fetchone()
    assert r["p_fair_at_bet"] is None and r["best_available"] is None
