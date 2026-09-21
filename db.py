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
    snapshot_type TEXT NOT NULL    -- 'open' | 'bettime' | 'close'
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


def init():
    con = connect()
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    print(f"DB initialized at {DB_PATH}")


if __name__ == "__main__":
    init()
