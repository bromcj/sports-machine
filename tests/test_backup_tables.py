"""backup.py must count every table db.py creates, or a backup can drop one
and still verify. It checked six of twelve until 2026-09-24."""
import re

import backup
import db


def test_the_backup_check_covers_every_table():
    created = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", db.SCHEMA))
    assert created == set(backup.TABLES)
