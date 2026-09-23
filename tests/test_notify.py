"""Alert delivery: the file is always right, and nothing leaks by default."""
import notify


def test_the_alerts_file_says_all_clear_when_healthy(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "ALERTS", tmp_path / "ALERTS.md")
    notify.deliver([{"level": "INFO", "check": "a", "detail": "fine"}], "2026-09-23T07:00:00+00:00")
    text = (tmp_path / "ALERTS.md").read_text(encoding="utf-8")
    assert "All clear" in text


def test_a_stale_alert_cannot_survive_a_healthy_run(tmp_path, monkeypatch):
    # The failure mode that matters: yesterday's problem still sitting in the
    # file, read as today's.
    monkeypatch.setattr(notify, "ALERTS", tmp_path / "ALERTS.md")
    notify.deliver([{"level": "ERROR", "check": "boom", "detail": "bad"}], "2026-09-23T07:00:00+00:00")
    assert "boom" in (tmp_path / "ALERTS.md").read_text(encoding="utf-8")
    notify.deliver([{"level": "INFO", "check": "a", "detail": "fine"}], "2026-09-23T08:00:00+00:00")
    text = (tmp_path / "ALERTS.md").read_text(encoding="utf-8")
    assert "boom" not in text and "All clear" in text


def test_nothing_is_sent_off_the_machine_without_opting_in(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "ALERTS", tmp_path / "ALERTS.md")
    monkeypatch.delenv(notify.NTFY_ENV, raising=False)
    # Make the local channel a no-op so the test does not pop a notification.
    monkeypatch.setattr(notify, "_toast", lambda *a: False)
    got = notify.deliver([{"level": "ERROR", "check": "x", "detail": "y"}],
                         "2026-09-23T07:00:00+00:00")
    assert got["ntfy"] is False


def test_ntfy_is_attempted_only_when_a_topic_is_set(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "ALERTS", tmp_path / "ALERTS.md")
    monkeypatch.setattr(notify, "_toast", lambda *a: False)
    calls = []
    monkeypatch.setenv(notify.NTFY_ENV, "a-topic")
    monkeypatch.setattr(notify, "_ntfy", lambda t, b: calls.append((t, b)) or True)
    got = notify.deliver([{"level": "CRITICAL", "check": "x", "detail": "y"}],
                         "2026-09-23T07:00:00+00:00")
    assert got["ntfy"] is True and len(calls) == 1


def test_warnings_are_listed_but_do_not_count_as_serious(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "ALERTS", tmp_path / "ALERTS.md")
    monkeypatch.setattr(notify, "_toast", lambda *a: False)
    got = notify.deliver([{"level": "WARNING", "check": "w", "detail": "look"}],
                         "2026-09-23T07:00:00+00:00")
    assert got["n_serious"] == 0
    assert "Worth a look" in (tmp_path / "ALERTS.md").read_text(encoding="utf-8")
