"""Export this run's odds snapshots and games to CSVs in archive/.

Run on the GitHub Actions runner after run_daily; the runner's DB is fresh,
so everything in it belongs to this run. Files are dated and typed so the
local merge can dedupe. Safe to run locally too.
"""
import csv
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import connect

ARCHIVE = Path(__file__).parent / "archive"


def export():
    ARCHIVE.mkdir(exist_ok=True)
    # Filename stamp, not a stored value - but utcnow() is deprecated.
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d-%H%M")
    con = connect()

    snaps = con.execute("SELECT * FROM odds_snapshots").fetchall()
    games = con.execute("SELECT * FROM games").fetchall()
    con.close()

    wrote = []
    if snaps:
        p = ARCHIVE / f"odds-{stamp}.csv"
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(snaps[0].keys())
            w.writerows([tuple(r) for r in snaps])
        wrote.append(p.name)
    if games:
        p = ARCHIVE / f"games-{stamp}.csv"
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(games[0].keys())
            w.writerows([tuple(r) for r in games])
        wrote.append(p.name)

    print(f"Exported {len(snaps)} snapshots, {len(games)} games -> "
          f"{', '.join(wrote) if wrote else 'nothing (empty run)'}")


if __name__ == "__main__":
    export()
