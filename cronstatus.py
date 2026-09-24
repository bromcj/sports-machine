"""Did the cloud actually run, and did it catch prices before first pitch?

Two questions this answers, both of which have bitten this project:

  1. Are the crons firing at all, and how late? GitHub queues scheduled
     workflows on shared runners. Measured here on 2026-09-22, the 14:00
     cron fired at 17:58 and the 22:40 cron at 00:53.
  2. Did the close pull land BEFORE first pitch? A price pulled after the
     first inning is an in-play price - a market that already knows part of
     the score - and it is worthless as a CLV anchor, which is the only
     reason the close pull exists.

Read-only and free. It shells out to `gh` for run history and reads the
archive CSVs already on disk. No API credits, no database writes.

  python run_daily.py cronstatus
"""
import csv
import datetime as dt
from feeds import parse_utc
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent
WORKFLOW = ROOT / ".github" / "workflows" / "daily.yml"
ARCHIVE = ROOT / "archive"

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:                                   # no tz database installed
    ET = dt.timezone.utc


def _clock(when: dt.datetime) -> str:
    """Windows strftime has no %-I, so strip the zero by hand."""
    s = when.astimezone(ET).strftime("%a %I:%M %p")
    return s.replace(" 0", " ", 1) if " 0" in s[:8] else s


def _span(minutes: float) -> str:
    m = int(round(minutes))
    if m < 60:
        return f"{m}m"
    return f"{m // 60}h{m % 60:02d}m"


def crons() -> list[tuple[int, int, str]]:
    """(hour, minute, note) for each cron in the workflow, in UTC.

    Parsed with a regex rather than a YAML library so this stays runnable on a
    checkout that has not installed anything beyond requirements.txt.
    """
    if not WORKFLOW.exists():
        return []
    out = []
    for line in WORKFLOW.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*-\s*cron:\s*'(\d+)\s+(\d+)\s+\*\s+\*\s+\*'\s*(?:#\s*(.*))?", line)
        if m:
            out.append((int(m.group(2)), int(m.group(1)), (m.group(3) or "").strip()))
    return out


def due_before(started: dt.datetime, sched: list[tuple[int, int, str]]):
    """The most recent scheduled firing at or before `started`.

    A run delayed past the NEXT cron would be misattributed here, but the
    alternative needs information GitHub does not put in the run record, and
    a delay that long is itself the headline.
    """
    best = None
    for days in (0, 1):
        day = (started - dt.timedelta(days=days)).date()
        for hour, minute, _ in sched:
            t = dt.datetime.combine(day, dt.time(hour, minute), dt.timezone.utc)
            if t <= started and (best is None or t > best):
                best = t
    return best


def schedule_changed() -> dt.datetime | None:
    """When the cron lines were last edited.

    Runs older than this fired under a DIFFERENT schedule, so measuring their
    lateness against today's crons invents a number. Better to say so than to
    print a confident wrong one.
    """
    try:
        p = subprocess.run(
            # -G: only commits that changed a `- cron:` line. Editing any other
            # step of the workflow does not change when the pulls are due.
            ["git", "log", "-1", "--format=%cI", "-G", "- cron:", "--",
             str(WORKFLOW)],
            capture_output=True, text=True, timeout=15, cwd=ROOT)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    out = p.stdout.strip()
    if p.returncode != 0 or not out:
        return None
    try:
        return dt.datetime.fromisoformat(out).astimezone(dt.timezone.utc)
    except ValueError:
        return None


def runs(limit: int = 20) -> tuple[list[dict], str | None]:
    # --workflow, or the test runs from every push crowd the scheduled pulls
    # out of the last `limit`: on 2026-09-24 all 20 were tests.yml and this
    # reported "none yet" with five scheduled runs in the history.
    cmd = ["gh", "run", "list", "--workflow", "daily.yml", "--limit", str(limit),
           "--json", "event,conclusion,status,startedAt,databaseId"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    except FileNotFoundError:
        return [], "gh is not installed or not on PATH"
    except subprocess.TimeoutExpired:
        return [], "gh timed out"
    if p.returncode != 0:
        return [], (p.stderr or "gh failed").strip().splitlines()[0]
    try:
        return json.loads(p.stdout), None
    except json.JSONDecodeError:
        return [], "could not parse gh output"


def capture(path: Path) -> dict | None:
    """Pregame vs in-play split for the games on that pull's own slate."""
    with open(path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("commence_time")]
    if not rows:
        return None
    # One cloud run is one pull per sport, seconds apart. A file whose
    # timestamps span hours is a dump of a whole database (export_snapshots
    # run locally), and measuring it as a pull reports history as tonight's.
    stamps = [t for t in (parse_utc(r["ts"]) for r in rows) if t]
    if stamps and (max(stamps) - min(stamps)).total_seconds() > 3600:
        return {"not_a_pull": True}
    pulled = parse_utc(rows[0]["ts"])
    lead = {}
    for r in rows:
        # Both sides through the one parser, so both come back aware UTC:
        # subtracting a naive time from an aware one raises TypeError.
        start = parse_utc(r["commence_time"])
        taken = parse_utc(r["ts"])
        if start is None or taken is None:
            continue
        mins = (start - taken).total_seconds() / 60
        # That pull's own slate: today's games, not the rest of the week's
        # board. An NFL game five days out is always "pregame" and would
        # flatter the number into meaninglessness.
        if -600 < mins < 720:
            lead[r["game_id"]] = mins
    if not lead:
        return None
    late = sorted(-v for v in lead.values() if v <= 0)
    return {"pulled": pulled.replace(tzinfo=dt.timezone.utc),
            "total": len(lead),
            "pregame": sum(1 for v in lead.values() if v > 0),
            "median_in": late[len(late) // 2] if late else 0}


def report() -> int:
    sched = crons()
    now = dt.datetime.now(dt.timezone.utc)
    print(f"\nCRON STATUS{' ' * 30}now {_clock(now)} ET\n")

    print("Scheduled pulls (.github/workflows/daily.yml)")
    if not sched:
        print("  none found - is the workflow file there?")
    for hour, minute, note in sched:
        t = dt.datetime.combine(now.date(), dt.time(hour, minute), dt.timezone.utc)
        print(f"  {t.astimezone(ET).strftime('%I:%M %p').lstrip('0'):>9s} ET   "
              f"{hour:02d}:{minute:02d} UTC   {note}")

    rows, err = runs()
    print("\nRecent scheduled runs")
    if err:
        print(f"  can't read run history: {err}")
        print("  (the capture check below still works - it reads archive/)")
    else:
        fired = [r for r in rows if r.get("event") == "schedule"][:6]
        if not fired:
            print("  none yet. Only pushes and hand-triggered runs so far.")
        else:
            changed = schedule_changed()
            stale = False
            print(f"  {'fired':22s} {'was due':14s} {'late':>7s}  result")
            for r in fired:
                started = parse_utc(r["startedAt"])
                if changed and started < changed:
                    stale = True
                    print(f"  {_clock(started) + ' ET':22s} "
                          f"{'(old schedule)':14s} {'-':>7s}  "
                          f"{r.get('conclusion') or r.get('status') or '?'}")
                    continue
                d = due_before(started, sched)
                late = _span((started - d).total_seconds() / 60) if d else "?"
                due = f"{d.astimezone(ET).strftime('%I:%M %p').lstrip('0')} ET" if d else "?"
                res = r.get("conclusion") or r.get("status") or "?"
                print(f"  {_clock(started) + ' ET':22s} {due:14s} {late:>7s}  {res}")
            if stale:
                print("  (old schedule) = fired before the crons were last"
                      " edited, so there is no")
                print("  honest 'late' to report for it.")

    print("\nPregame capture - did the pull beat first pitch?")
    files = sorted(ARCHIVE.glob("odds-*.csv"))[-4:] if ARCHIVE.exists() else []
    if not files:
        print("  no archive/odds-*.csv yet. Run `git pull` first.")
    for p in files:
        c = capture(p)
        if not c:
            print(f"  {p.name}  (no games on its own slate)")
            continue
        if c.get("not_a_pull"):
            print(f"  {p.name}  (not a single pull - skipped)")
            continue
        pct = 100 * c["pregame"] / c["total"]
        flag = "" if pct == 100 else ("  <-- in-play, unusable for CLV"
                                      if pct < 60 else "  <-- some missed")
        ratio = f"{c['pregame']}/{c['total']}"
        print(f"  {_clock(c['pulled']) + ' ET':20s} {ratio:>5s} pregame "
              f"({pct:3.0f}%){flag}")
        if c["pregame"] < c["total"]:
            print(f"{'':22s} {c['total'] - c['pregame']:>2d} already underway, "
                  f"median {_span(c['median_in'])} in")

    print("\nWhat good looks like: runs a few minutes late, not hours, and")
    print("every pull at 100% pregame. Anything caught after first pitch is")
    print("an in-play price and contributes nothing to the CLV gate.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(report())
