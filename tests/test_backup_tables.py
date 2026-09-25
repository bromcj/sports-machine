"""backup.py must count every table db.py creates, or a backup can drop one
and still verify. It checked six of twelve until 2026-09-24.

And it must count the same moment it copies: the scanner's polling loop
writes around the clock."""
import re
import sqlite3
import threading

import backup
import db


def test_the_backup_check_covers_every_table():
    created = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", db.SCHEMA))
    assert created == set(backup.TABLES)


def test_a_write_just_after_the_copy_does_not_fail_a_good_backup(tmp_path, monkeypatch,
                                                                  capsys):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "machine.db")
    monkeypatch.setattr(backup, "DB_PATH", tmp_path / "machine.db")
    monkeypatch.setenv("SPORTS_MACHINE_BACKUP_DIR", str(tmp_path / "backups"))
    db.init()
    con = db.connect()
    con.execute("INSERT INTO games (game_id, sport, game_date, away, home)"
                " VALUES ('g1', 'nba', '2026-11-03', 'A', 'H')")
    con.commit()
    con.close()
    writers = []

    def loop_writes(done):
        w = sqlite3.connect(db.DB_PATH, timeout=30)
        w.execute("INSERT INTO api_usage (ts, sport, endpoint)"
                  " VALUES ('2026-11-03T15:00:00+00:00', 'nba', 'scanner/odds')")
        w.commit()
        w.close()
        done.set()

    class Con:
        """backup.py's connections, with the polling loop writing the moment
        a copy is finished."""
        def __init__(self, c):
            self._c = c

        def __getattr__(self, name):
            return getattr(self._c, name)

        def backup(self, target, **kw):
            self._c.backup(getattr(target, "_c", target), **kw)
            done = threading.Event()
            writers.append(threading.Thread(target=loop_writes, args=(done,)))
            writers[-1].start()
            done.wait(1)               # lands at once, unless a read holds it off

    class Sqlite3:
        def __getattr__(self, name):
            return getattr(sqlite3, name)

        @staticmethod
        def connect(*a, **k):
            return Con(sqlite3.connect(*a, **k))

    monkeypatch.setattr(backup, "sqlite3", Sqlite3())
    try:
        dest = backup.take()
    finally:
        for t in writers:
            t.join(30)
    assert dest.exists()
    con = db.connect()
    assert con.execute("SELECT COUNT(*) FROM api_usage").fetchone()[0] == 1   # it wrote
    con.close()
