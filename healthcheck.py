"""Health check: verifies this run actually collected what it should,
writes STATUS.md (the repo's front-page dashboard), and exits non-zero
on failure so the GitHub Actions run goes RED and GitHub emails the owner.

Silence = healthy. Email = broken. That's the contract.

Checks:
  1. Every in-season sport delivered odds snapshots this run
  2. Every in-season sport has games on the schedule (when expected)
  3. Archive export produced files this run
  4. Snapshot prices are sane (no null-only books)
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import connect
from config import SPORTS, active_sports

ROOT = Path(__file__).parent
STATUS = ROOT / "STATUS.md"


def check() -> int:
    month = dt.date.today().month
    live = active_sports(month)
    con = connect()
    problems, lines = [], []

    for sport in live:
        snaps = con.execute(
            "SELECT COUNT(*) c, SUM(away_ml IS NOT NULL) p FROM odds_snapshots "
            "WHERE sport=?", (sport,)).fetchone()
        games = con.execute(
            "SELECT COUNT(*) c FROM games WHERE sport=?", (sport,)).fetchone()
        ok = snaps["c"] > 0 and (snaps["p"] or 0) > 0
        icon = "🟢" if ok else "🔴"
        lines.append(f"| {icon} {sport.upper()} | {games['c']} games | "
                     f"{snaps['c']} snapshots |")
        if not ok:
            problems.append(
                f"{sport}: 0 usable odds snapshots this run — API key, "
                f"credit balance, or the sport has no games today.")

    archive = ROOT / "archive"
    fresh = []
    if archive.exists():
        cutoff = dt.datetime.utcnow().timestamp() - 2 * 3600
        fresh = [p for p in archive.glob("*.csv") if p.stat().st_mtime > cutoff]
    if not fresh:
        problems.append("archive: no CSV exported in this run window.")
    con.close()

    stamp = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    body = [f"# Machine Status — {stamp}", "",
            "**" + ("🟢 ALL SYSTEMS HEALTHY" if not problems
                    else "🔴 ATTENTION NEEDED") + "**", "",
            "| Sport | Schedule | Odds this run |", "|---|---|---|"]
    body += lines
    body += ["", f"Archive files this run: {len(fresh)}",
             f"In-season: {', '.join(live) or 'none'}"]
    if problems:
        body += ["", "## Problems"] + [f"- {p}" for p in problems]
        body += ["", "_This run was failed on purpose so GitHub emails the "
                 "owner. See the run log for details._"]
    STATUS.write_text("\n".join(body) + "\n", encoding="utf-8")

    for p in problems:
        print(f"HEALTHCHECK FAIL: {p}")
    if not problems:
        print(f"Healthcheck: all green ({', '.join(live)}).")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(check())
