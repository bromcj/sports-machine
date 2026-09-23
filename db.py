"""SQLite storage layer. Single source of truth for the machine.
Every table carries a `sport` column — one DB, all leagues.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "machine.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    game_id TEXT PRIMARY KEY,
    sport TEXT NOT NULL,
    game_date TEXT NOT NULL,
    away TEXT NOT NULL,
    home TEXT NOT NULL,
    away_starter TEXT,             -- SP (MLB) / QB or goalie if tracked
    home_starter TEXT,
    away_score INTEGER,
    home_score INTEGER,
    status TEXT DEFAULT 'scheduled'
);

CREATE TABLE IF NOT EXISTS odds_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    sport TEXT NOT NULL,
    ts TEXT NOT NULL,
    book TEXT NOT NULL,
    away_ml INTEGER,
    home_ml INTEGER,
    snapshot_type TEXT NOT NULL,   -- 'open' | 'bettime' | 'close'
    commence_time TEXT             -- ISO first-pitch time, from the odds API.
                                   -- snapshot_type records WHICH PULL this came
                                   -- from; this records how close to the actual
                                   -- start it was. Only the second one lets you
                                   -- identify a true closing line per game.
);

CREATE TABLE IF NOT EXISTS features (
    game_id TEXT PRIMARY KEY,
    sport TEXT NOT NULL,
    asof_ts TEXT NOT NULL,
    payload TEXT NOT NULL          -- JSON feature vector
);

CREATE TABLE IF NOT EXISTS predictions (
    game_id TEXT PRIMARY KEY,
    sport TEXT NOT NULL,
    ts TEXT NOT NULL,
    model_version TEXT NOT NULL,
    proj_margin REAL NOT NULL,     -- home minus away, sport-native units
    home_win_prob REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS merged_files (
    name TEXT PRIMARY KEY,         -- archive/ filename, e.g. odds-2026-09-23-0053.csv
    bytes INTEGER NOT NULL,        -- size when merged; a changed size means re-read
    rows INTEGER NOT NULL,
    merged_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bets (
    bet_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    game_id TEXT NOT NULL,
    sport TEXT NOT NULL,
    side TEXT NOT NULL,
    book TEXT NOT NULL,
    line_taken INTEGER NOT NULL,
    stake REAL NOT NULL,
    model_prob REAL NOT NULL,
    novig_market_prob REAL NOT NULL,
    edge REAL NOT NULL,
    kelly_fraction REAL NOT NULL,
    closing_line INTEGER,
    novig_closing_prob REAL,
    clv_pct REAL,
    result TEXT,
    pnl REAL,
    model_version TEXT
);
"""


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


# Columns added after the first release. Each is applied to an existing DB if
# missing, so `python db.py` is safe to re-run and upgrades in place.
MIGRATIONS = [
    ("odds_snapshots", "commence_time", "TEXT"),
    # MLBAM player ids for the probable starters. The names alone cannot be
    # joined to Statcast, which keys pitchers by id - so without these there is
    # no way to look up a probable starter's recent form before a game.
    ("games", "away_starter_id", "INTEGER"),
    ("games", "home_starter_id", "INTEGER"),
]


# Indexes. Created by init(), so `python db.py` adds them to an existing DB.
#
# Without these EVERY hot query is a full table scan - measured with EXPLAIN
# QUERY PLAN on the live DB, all five of them. That is invisible at a thousand
# rows and fatal at a hundred thousand, because merge_archive.py does one
# dedupe probe per archived row against a table growing at the same rate. That
# is O(n^2): measured, a year of collection takes the daily merge from instant
# to 84 minutes. With the first index below it is 7 seconds.
#
# The first one is UNIQUE, which also turns deduplication from a convention
# enforced in one hand-written SELECT inside merge_archive.py into something
# the database will not let any writer violate.
INDEXES = [
    # (game_id, ts, book, snapshot_type) identifies one price from one book in
    # one pull. A second row with that key is the same observation twice.
    ("ux_snap_dedupe", "CREATE UNIQUE INDEX IF NOT EXISTS ux_snap_dedupe"
                       " ON odds_snapshots(game_id, ts, book, snapshot_type)"),
    # healthcheck counts per sport; 'latest snapshot for X' sorts by ts.
    ("ix_snap_sport_ts", "CREATE INDEX IF NOT EXISTS ix_snap_sport_ts"
                         " ON odds_snapshots(sport, ts)"),
    # odds_twin() and every 'today's slate' query filter on exactly this pair.
    ("ix_games_sport_date", "CREATE INDEX IF NOT EXISTS ix_games_sport_date"
                            " ON games(sport, game_date)"),
]
# closing_snapshot() filters odds_snapshots on game_id alone; that is the
# leading column of ux_snap_dedupe, so SQLite uses it. No separate index.


def index(con):
    """Create any missing index. Idempotent.

    A UNIQUE index fails to build if the table already violates it, which is
    information worth surfacing rather than swallowing: it means duplicate
    snapshots are already in there and the dedupe was not doing its job.
    """
    have = {r["name"] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    added = []
    for name, ddl in INDEXES:
        if name in have:
            continue
        try:
            con.execute(ddl)
            added.append(name)
        except sqlite3.IntegrityError as e:
            raise SystemExit(
                f"Cannot create {name}: {e}\n"
                "The table already contains rows that violate it. Inspect with:\n"
                "  SELECT game_id, ts, book, snapshot_type, COUNT(*) c\n"
                "  FROM odds_snapshots GROUP BY 1,2,3,4 HAVING c > 1;")
    return added


def migrate(con):
    """Add any columns a pre-existing DB is missing. Idempotent."""
    applied = []
    for table, column, coltype in MIGRATIONS:
        cols = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
        if not cols:
            continue          # table doesn't exist yet; SCHEMA just created it
        if column not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
            applied.append(f"{table}.{column}")
    return applied


def init():
    con = connect()
    con.executescript(SCHEMA)
    applied = migrate(con)
    indexed = index(con)
    con.commit()
    con.close()
    if applied:
        print(f"Migrated: added {', '.join(applied)}")
    if indexed:
        print(f"Indexed: created {', '.join(indexed)}")
    print(f"DB initialized at {DB_PATH}")


if __name__ == "__main__":
    init()
