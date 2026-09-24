"""Export this run's odds snapshots and games to CSVs in archive/.

Run on the GitHub Actions runner after run_daily; the runner's DB is fresh,
so everything in it belongs to this run. Files are dated and typed so the
local merge can dedupe.

NOT safe to run locally. A local database holds every snapshot ever
collected, so an export there writes all of it into archive/ as if it were one
pull - which is how the command sweep of 2026-09-24 put two 41 MB dumps of the
paid historical odds into the public repo. It refuses unless it is running in
GitHub Actions, or is given --local on purpose.
"""
import csv
import datetime as dt
import os
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
    if os.environ.get("GITHUB_ACTIONS") != "true" and "--local" not in sys.argv:
        raise SystemExit(
            "REFUSED: export_snapshots.py dumps the WHOLE database into "
            "archive/, which is only right on the cloud runner's fresh one. "
            "Pass --local if you really mean it.")
    export()
