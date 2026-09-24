"""A failed odds pull must make the run fail, not read as an empty slate.

ingest/odds.pull() printed "[mlb] pull failed" and carried on, so a 401 or
429 left the cloud run green: the fresh database then held no games, and
healthcheck counted that as "no games scheduled".
"""
import pytest
import requests

import run_daily
from ingest import odds


def test_a_failed_sport_is_returned(monkeypatch):
    monkeypatch.setattr(odds, "API_KEY", "not-a-real-key")
    monkeypatch.setattr(odds, "active_sports", lambda month: ["mlb", "nfl"])

    def fake(sport, snapshot_type):
        if sport == "mlb":
            raise requests.HTTPError("401 Unauthorized")
        return 0
    monkeypatch.setattr(odds, "pull_sport", fake)
    assert odds.pull("close") == ["mlb"]


def test_close_exits_non_zero_when_a_pull_failed(monkeypatch):
    monkeypatch.setattr(run_daily.odds, "pull", lambda kind: ["mlb"])
    with pytest.raises(SystemExit) as e:
        run_daily.close()
    assert "mlb" in str(e.value)
