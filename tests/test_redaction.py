"""A credential must never reach a log."""
import pytest
from ingest.http import redact


@pytest.mark.parametrize("text", [
    "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds?apiKey=abc123secret&regions=us",
    "HTTPSConnectionPool: Max retries with url: /odds?api_key=deadbeef",
    "failed for token=hunter2",
    "?key=s3cr3t&markets=h2h",
])
def test_the_key_is_removed(text):
    out = redact(text)
    for leaked in ("abc123secret", "deadbeef", "hunter2", "s3cr3t"):
        assert leaked not in out
    assert "***" in out


def test_the_rest_of_the_message_survives():
    out = redact("GET /odds?apiKey=abc123&regions=us failed: 502 Bad Gateway")
    assert "502 Bad Gateway" in out and "regions=us" in out


def test_a_message_with_no_secret_is_untouched():
    msg = "ConnectionError: name resolution failed"
    assert redact(msg) == msg


def test_it_accepts_an_exception_object():
    assert "***" in redact(Exception("boom at ?apiKey=zzz"))
