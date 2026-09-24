"""Back up the database, and prove the backup actually opens.

  python backup.py             take one, verify it, prune old ones
  python backup.py --list      what exists, and how old
  python backup.py --verify F  re-check one file
  python backup.py --restore F put it back (asks first)

data/ is the only thing in this project that exists nowhere else. Everything
in the repo is on GitHub, and most of data/ can be rebuilt - odds and games
from archive/, Statcast from backfill.py, the models by retraining. What
cannot be rebuilt is the `bets` table: your actual wagers, the line you took,
and the CLV that follows from it. There is no second copy of that anywhere,
and gate 2 is counted in graded bets.

Where they go: OneDrive if it exists, otherwise your home folder. That is
deliberate, and not a contradiction of moving the repo out of OneDrive - a
live SQLite file held open by a sync client is what corrupts; a finished .db
written once and never touched again is exactly what a sync client is for.
Override with the SPORTS_MACHINE_BACKUP_DIR environment variable.

Two things this does that a file copy does not:

  online backup   sqlite3's backup API takes a consistent snapshot of a
                  database that is being written to. `copy machine.db` during
                  a write gives you a torn file that looks fine until the day
                  you need it.
  verification    every backup is reopened, integrity-checked, and its row
                  counts compared against the source. An unverified backup is
                  not a backup, it is a hope.
"""
import datetime as dt
import os
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import DB_PATH

KEEP = 14                      # newest files kept (not days); ~92 MB each now
# Every table db.py creates. The row-count check used to cover six, so a
# backup missing market_close (the paid closes gate 1 is judged on) or
# odds_history_progress (which snapshots were bought) still "verified".
TABLES = ["games", "odds_snapshots", "features", "models", "predictions",
          "market_scores", "api_usage", "probables_history",
          "odds_history_progress", "market_close", "merged_files", "bets"]


def backup_dir() -> Path:
    env = os.environ.get("SPORTS_MACHINE_BACKUP_DIR")
    if env:
        return Path(env)
    onedrive = os.environ.get("OneDrive") or os.environ.get("ONEDRIVE")
    if onedrive and Path(onedrive).is_dir():
        return Path(onedrive) / "sports-machine-backups"
    return Path.home() / "sports-machine-backups"


def counts(path: Path) -> dict:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        have = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in TABLES if t in have}
    finally:
        con.close()


def verify(path: Path, expect: dict | None = None) -> tuple[bool, str]:
    """Open the backup for real and check it. This is the restore test."""
    if not path.exists():
        return False, "file does not exist"
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            ok = con.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            con.close()
        if ok != "ok":
            return False, f"integrity_check said: {ok}"
        got = counts(path)
    except sqlite3.DatabaseError as e:
        return False, f"will not open: {e}"
    if not got:
        return False, "opens, but has none of the expected tables"
    # Opening is not enough. A file can be a perfectly valid SQLite database
    # and still be useless as a backup - the empty one you would get from
    # backing up a fresh checkout, or from a run that created the schema and
    # then failed. take() catches that by comparing row counts to the source;
    # a standalone --verify has nothing to compare against, so it checks that
    # the essentials are present and populated.
    missing = [t for t in ("games", "odds_snapshots") if t not in got]
    if missing:
        return False, f"opens, but is missing {', '.join(missing)}"
    if got["games"] == 0:
        return False, "opens, but holds no games — an empty database is not a backup"
    if expect is not None:
        drift = {t: (expect[t], got.get(t)) for t in expect
                 if got.get(t) != expect[t]}
        if drift:
            return False, "row counts differ from source: " + ", ".join(
                f"{t} {a}->{b}" for t, (a, b) in drift.items())
    return True, ", ".join(f"{t} {n:,}" for t, n in got.items())


def take() -> Path:
    if not DB_PATH.exists():
        raise SystemExit(f"No database at {DB_PATH} — nothing to back up.")
    dest_dir = backup_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d-%H%M")
    dest = dest_dir / f"machine-{stamp}.db"

    source = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    target = sqlite3.connect(dest)
    try:
        # The online backup API, not shutil.copy: this is consistent even if
        # something is mid-write, which a plain file copy is not.
        source.backup(target)
    finally:
        target.close()
        before = counts(DB_PATH)
        source.close()

    ok, detail = verify(dest, expect=before)
    if not ok:
        dest.unlink(missing_ok=True)
        raise SystemExit(f"Backup FAILED verification and was deleted: {detail}")
    size = dest.stat().st_size / 1e6
    print(f"Backed up to {dest}  ({size:.1f} MB)")
    print(f"  verified: {detail}")
    return dest


def prune(keep: int = KEEP) -> int:
    old = sorted(backup_dir().glob("machine-*.db"))[:-keep] if keep else []
    for p in old:
        p.unlink()
    if old:
        print(f"  pruned {len(old)} backup(s) older than the last {keep}")
    return len(old)


def listing():
    d = backup_dir()
    files = sorted(d.glob("machine-*.db"), reverse=True)
    print(f"\nBackups in {d}")
    if not files:
        print("  none yet — run `python backup.py`\n")
        return
    now = dt.datetime.now()
    for p in files:
        age = now - dt.datetime.fromtimestamp(p.stat().st_mtime)
        hrs = age.total_seconds() / 3600
        when = f"{hrs:.0f}h ago" if hrs < 48 else f"{hrs / 24:.0f}d ago"
        print(f"  {p.name:28s} {p.stat().st_size / 1e6:6.1f} MB  {when:>8s}")
    newest = dt.datetime.fromtimestamp(files[0].stat().st_mtime)
    stale = (now - newest).days
    print(f"\n  {len(files)} backup(s); newest is {stale} day(s) old.")
    if stale >= 7:
        print("  That is getting old. Run `python backup.py`.")
    print()


def restore(path: Path):
    ok, detail = verify(path)
    if not ok:
        raise SystemExit(f"Refusing to restore: {detail}")
    print(f"Restore {path}\n  -> {DB_PATH}")
    print(f"  backup holds: {detail}")
    if DB_PATH.exists():
        print(f"  current holds: {', '.join(f'{t} {n:,}' for t, n in counts(DB_PATH).items())}")
        print("\nThe current database will be REPLACED.")
    if input("Type 'restore' to continue: ").strip() != "restore":
        raise SystemExit("Cancelled. Nothing was changed.")
    if DB_PATH.exists():
        # Never destroy the thing being replaced until the replacement is in.
        aside = DB_PATH.with_suffix(
            f".replaced-{dt.datetime.now():%Y%m%d-%H%M%S}.db")
        shutil.move(DB_PATH, aside)
        print(f"  previous database kept at {aside}")
    shutil.copy2(path, DB_PATH)
    ok, detail = verify(DB_PATH)
    print(f"Restored. {'OK: ' + detail if ok else 'PROBLEM: ' + detail}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--list" in args:
        listing()
    elif "--verify" in args:
        f = Path(args[args.index("--verify") + 1])
        ok, detail = verify(f)
        print(f"{'OK  ' if ok else 'FAIL'} {f}: {detail}")
        raise SystemExit(0 if ok else 1)
    elif "--restore" in args:
        restore(Path(args[args.index("--restore") + 1]))
    else:
        take()
        prune()
