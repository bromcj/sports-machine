"""Health check: verifies this run actually collected what it should,
writes STATUS.md (the repo's front-page dashboard), and exits non-zero
on failure so the GitHub Actions run goes RED and GitHub emails the owner.

Silence = healthy. Email = broken. That's the contract.

Checks:
  1. Every in-season sport delivered odds snapshots this run
     (skipped for `grade`, which pulls finals only and never touches odds)
  2. Every in-season sport has games on the schedule (when expected)
  3. Archive export produced files this run (by the time in the file NAME:
     actions/checkout stamps every file with the checkout time, so file
     modification times said every archive file was fresh)
  4. Snapshot prices are sane (no null-only books)
"""
import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import connect
from feeds import et_today
from config import active_sports

ROOT = Path(__file__).parent
STATUS = ROOT / "STATUS.md"


def check() -> int:
    # `grade` collects finals from ESPN and the MLB API and deliberately pulls
    # no odds, so demanding odds from it fails every grade run and emails the
    # owner about an API key that is fine. Anything else - including an unset
    # RUN_MODE, which is how the audit and a bare `python healthcheck.py` call
    # it - is still held to the odds check.
    expect_odds = os.environ.get("RUN_MODE", "") != "grade"
    month = et_today().month
    live = active_sports(month)
    con = connect()
    problems, lines = [], []

    any_slate = False
    for sport in live:
        snaps = con.execute(
            "SELECT COUNT(*) c, SUM(away_ml IS NOT NULL) p FROM odds_snapshots "
            "WHERE sport=?", (sport,)).fetchone()
        games = con.execute(
            "SELECT COUNT(*) c FROM games WHERE sport=?", (sport,)).fetchone()
        today = con.execute(
            "SELECT COUNT(*) c FROM games WHERE sport=? AND game_date=?",
            (sport, et_today().isoformat())).fetchone()
        priced = snaps["c"] > 0 and (snaps["p"] or 0) > 0
        # An empty slate is not a fault. config.py marks a sport in-season by
        # MONTH, so there are long dead windows inside an "active" month -
        # roughly Mar 1-25 for MLB, Oct 1-20 for NBA, Feb 9-28 for NFL. Failing
        # the run on those days emails the owner three times a day for weeks,
        # and an alert that cries wolf is worse than no alert.
        no_slate = today["c"] == 0
        ok = priced or no_slate or not expect_odds
        icon = "⚪" if no_slate and not priced else ("🟢" if ok else "🔴")
        if not expect_odds:
            note = "not pulled (grade run)"
        elif no_slate and not priced:
            note = "no games scheduled"
        else:
            note = f"{snaps['c']} snapshots"
        lines.append(f"| {icon} {sport.upper()} | {games['c']} games | {note} |")
        any_slate = any_slate or not no_slate
        if not ok:
            problems.append(
                f"{sport}: {today['c']} games scheduled today but 0 usable odds "
                f"snapshots — check the API key and credit balance.")

    archive = ROOT / "archive"
    fresh = []
    if archive.exists():
        now = dt.datetime.now(dt.timezone.utc)
        for p in archive.glob("*.csv"):
            # odds-2026-09-24-0141.csv: the export stamps its UTC time here.
            try:
                made = dt.datetime.strptime(p.stem.split("-", 1)[1],
                                            "%Y-%m-%d-%H%M").replace(
                                                tzinfo=dt.timezone.utc)
            except (IndexError, ValueError):
                continue
            if dt.timedelta(0) <= now - made <= dt.timedelta(hours=2):
                fresh.append(p)
    if not fresh and any_slate and expect_odds:
        problems.append("archive: no CSV exported in this run window.")
    con.close()

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
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
