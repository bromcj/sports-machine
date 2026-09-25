"""The scanner scoreboard: per strategy, per venue, in total, paper and placebo
apart, and the capital still locked."""
import pytest

import db
from model import validation as v
from scanner import scoreboard


@pytest.fixture
def con(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(v, "PATH", tmp_path / "validation.json")
    db.init()
    capsys.readouterr()
    c = db.connect()
    yield c
    c.close()


def _pos(con, oid, strategy, venue, mode="paper", stake=10.0, ev=0.05, days=2.0,
         result=None, pnl=None):
    con.execute("INSERT INTO paper_positions (order_id, strategy, mode, venue, market_id,"
                " outcome, contracts, avg_price, fee, stake, opened_at, resolves_at,"
                " days_to_resolution, ev, annualized_ev, result, pnl) VALUES"
                " (?,?,?,?,'m','yes',20,0.5,0,?,'2026-11-01T00:00:00.000000+00:00',"
                " '2026-12-03T00:00:00.000000+00:00',?,?,?,?,?)",
                (oid, strategy, mode, venue, stake, days, ev, ev / days * 365, result, pnl))


def test_it_splits_by_strategy_by_venue_and_keeps_placebo_apart(con):
    _pos(con, 1, "alpha", "kalshi")
    _pos(con, 2, "alpha", "kalshi", result="win", pnl=10.0)
    _pos(con, 3, "alpha", "kalshi", mode="placebo", ev=-0.02)
    _pos(con, 4, "beta", "sportsbook:draftkings", stake=20.0, ev=0.01, days=0.25)
    s = scoreboard.summary(con)
    a = s["by_strategy"][("alpha", "paper")]
    assert (a["positions"], a["open"], a["settled"]) == (2, 1, 1)
    assert a["locked"] == 10.0 and a["pnl"] == 10.0 and a["roi"] == 1.0
    assert s["by_strategy"][("alpha", "placebo")]["ev"] == pytest.approx(-0.02)
    assert set(s["by_venue"]) == {("kalshi", "paper"), ("kalshi", "placebo"),
                                  ("sportsbook:draftkings", "paper")}
    assert s["total"]["paper"]["positions"] == 3
    b = s["by_strategy"][("beta", "paper")]
    assert b["annualized"] == pytest.approx(0.01 / 0.25 * 365)     # ~14.6 a year
    assert s["locked"]["total"] == 30.0 and s["locked"]["by_month"] == {"2026-12": 30.0}
    assert s["verdicts"]["alpha"] == "alpha: no gate record yet"


def test_it_says_so_when_the_tables_are_not_there(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "bare.db")
    c = db.connect()
    lines = []
    scoreboard.report(c, out=lines.append)
    c.close()
    assert any("do not exist here yet" in ln for ln in lines)
