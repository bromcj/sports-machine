"""Where the data lives. One definition, honouring one environment variable.

Thirteen files each worked out `Path(__file__).parent / "data"` for themselves,
with four different numbers of `.parent` depending on how deep they sat. That
is fine while there is exactly one checkout, and it is the thing that makes a
second one impossible: you cannot point a production copy at the same database
without editing thirteen files.

    SPORTS_MACHINE_DATA_DIR     absolute path to the data directory
                                unset -> ./data, exactly as before

Nothing changes for anyone who does not set it. That is deliberate: this is
plumbing for the production split in docs/production-setup.md, not a new thing
to configure.

Note for tests and audit.py: db.DB_PATH stays a module-level name that can be
reassigned to point at a temp file. This module supplies its default, it does
not take that ability away.
"""
import os
from pathlib import Path

ROOT = Path(__file__).parent

_env = os.environ.get("SPORTS_MACHINE_DATA_DIR", "").strip()
DATA_DIR = Path(_env).expanduser().resolve() if _env else (ROOT / "data")

DB_PATH = DATA_DIR / "machine.db"
STATCAST_DIR = DATA_DIR / "statcast"
MODELS_DIR = DATA_DIR / "models"
NFL_DIR = DATA_DIR / "nfl"
RAW_DIR = DATA_DIR / "raw"


def training_table(sport: str) -> Path:
    return DATA_DIR / f"training_{sport}.parquet"


def describe() -> str:
    where = "SPORTS_MACHINE_DATA_DIR" if _env else "default (./data)"
    return f"data: {DATA_DIR}  [{where}]"


if __name__ == "__main__":
    print(describe())
    for name in ("DB_PATH", "STATCAST_DIR", "MODELS_DIR", "NFL_DIR", "RAW_DIR"):
        p = globals()[name]
        print(f"  {name:14s} {p}   {'exists' if p.exists() else 'missing'}")
