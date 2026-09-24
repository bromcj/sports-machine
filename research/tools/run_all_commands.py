"""1.5c Run every command once, against the COPIED data, and record what happened.

    python research/tools/run_all_commands.py > docs/reports/command-run-log.md

In the order a scheduled day runs them. Points SPORTS_MACHINE_DATA_DIR at
data_phase1/, so nothing here touches the live database.

TWO COMMANDS ARE DELIBERATELY NOT RUN: `morning` and `close`. They are the only
two that spend API credits, CLAUDE.md says never to run either just to test a
change, and this brief's credit cap allows spending in Phase 0 collection only.
Their path is exercised instead by dispatching the cloud workflow with
`mode=grade`, which pulls no odds - which is the method CLAUDE.md itself
recommends.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent

# (label, argv, why-if-skipped)
COMMANDS = [
    ("morning", None, "costs 1 credit per in-season sport"),
    ("predict", [sys.executable, "run_daily.py", "predict"], None),
    ("paper", [sys.executable, "run_daily.py", "paper"], None),
    ("close", None, "costs 1 credit per in-season sport"),
    ("finals", [sys.executable, "run_daily.py", "finals"], None),
    ("grade", [sys.executable, "run_daily.py", "grade"], None),
    ("market", [sys.executable, "run_daily.py", "market"], None),
    ("picks", [sys.executable, "run_daily.py", "picks"], None),
    ("monitor", [sys.executable, "monitor.py"], None),
    ("cronstatus", [sys.executable, "cronstatus.py"], None),
    ("healthcheck", [sys.executable, "healthcheck.py"], None),
    ("merge_archive", [sys.executable, "merge_archive.py"], None),
    ("export_snapshots", [sys.executable, "export_snapshots.py"], None),
    ("backup", [sys.executable, "backup.py"], None),
    ("dashboard", [sys.executable, "dashboard.py"], None),
    ("db (migrations)", [sys.executable, "db.py"], None),
    ("db again (must be a no-op)", [sys.executable, "db.py"], None),
    ("validation", [sys.executable, "model/validation.py"], None),
    ("collect --plan", [sys.executable, "props/collect.py", "--plan"], None),
    ("integration matrix --check",
     [sys.executable, "research/tools/integration_matrix.py", "--check"], None),
    ("audit", [sys.executable, "audit.py"], None),
]


def env():
    e = dict(os.environ)
    e["SPORTS_MACHINE_DATA_DIR"] = str(ROOT / "data_phase1")
    e["PYTHONIOENCODING"] = "utf-8"
    return e


# validation.json is NOT under SPORTS_MACHINE_DATA_DIR - db.py keeps it at the
# repo root deliberately, because it is a decision record rather than derived
# data. So `paper`, `grade` and `market` rewrite the REAL one even when every
# other write is redirected to a copy, and Phase 1's ground rule says not to
# touch it before the merge. Saved and restored around the sweep.
PROTECTED = ["validation.json", "STATUS.md", "dashboard.html", "dashboard.png"]


def main() -> int:
    saved = {}
    for name in PROTECTED:
        p = ROOT / name
        if p.exists():
            saved[name] = p.read_bytes()
    try:
        return _sweep()
    finally:
        for name, blob in saved.items():
            (ROOT / name).write_bytes(blob)


def _sweep() -> int:
    print("# Command run log\n")
    print("Every command, in the order a scheduled day runs them, against "
          "`data_phase1/` - a copy taken from a verified backup. The live "
          "database is not touched.\n")
    print("| # | command | exit | seconds | first line of output |")
    print("|---|---|---|---|---|")
    failures = []
    for i, (label, argv, skip) in enumerate(COMMANDS, 1):
        if argv is None:
            print(f"| {i} | `{label}` | **skipped** | — | {skip} |")
            continue
        t0 = time.time()
        r = subprocess.run(argv, cwd=ROOT, env=env(), capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=3600)
        dur = time.time() - t0
        out = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        first = next((l.strip() for l in out if l.strip()), "")
        mark = "0" if r.returncode == 0 else f"**{r.returncode}**"
        if r.returncode != 0:
            failures.append((label, r.returncode, out[-8:]))
        print(f"| {i} | `{' '.join(argv[1:])}` | {mark} | {dur:.1f} | "
              f"{first[:92].replace('|', '\\|')} |")
    if failures:
        print("\n## Non-zero exits\n")
        for label, code, tail in failures:
            print(f"\n**{label}** exited {code}:\n")
            print("```")
            for l in tail:
                print(l)
            print("```")
    else:
        print("\nEvery command that was run exited **0**.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
