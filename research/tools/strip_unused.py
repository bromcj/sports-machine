"""Remove the imports pyflakes says are unused. One pass, reversible by git.

    python research/tools/strip_unused.py --dry
    python research/tools/strip_unused.py --apply

Deletes only the NAME pyflakes named, and only from the line it named; the
whole line goes only when that name was the only thing on it. Anything it is
not sure about it leaves and prints.

audit.py is skipped entirely. It imports modules inside functions for the
express purpose of proving they import cleanly on a bare checkout - which looks
exactly like an unused import to a parser, and is the whole point of the check.
"""
import argparse
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
SKIP_FILES = {"audit.py"}


def findings():
    r = subprocess.run([sys.executable, "-m", "pyflakes", "."],
                       cwd=ROOT, capture_output=True, text=True)
    out = defaultdict(list)
    for line in (r.stdout or "").splitlines():
        m = re.match(r"^\.[\\/](.+?):(\d+):\d+: '(.+?)' imported but unused$",
                     line.strip())
        if not m:
            continue
        f, ln, name = m.group(1).replace("\\", "/"), int(m.group(2)), m.group(3)
        if f.startswith("data_phase1") or Path(f).name in SKIP_FILES:
            continue
        # "pathlib.Path" -> Path ; "datetime as dt" -> dt ; "x.y as z" -> z
        bound = name.split(" as ")[-1].split(".")[-1].strip()
        out[f].append((ln, bound))
    return out


def strip(path: Path, items, apply: bool) -> int:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    drop_lines, edits = set(), 0
    for ln, name in sorted(items, reverse=True):
        if ln - 1 >= len(lines):
            continue
        line = lines[ln - 1]
        if not line.lstrip().startswith(("import ", "from ")):
            print(f"  ? {path}:{ln} not an import line, left alone")
            continue
        # A PARENTHESISED MULTI-LINE IMPORT. The names are on the FOLLOWING
        # lines, so `line.split("import")[-1]` is empty, which the one-name
        # rule below read as "the only name on the line" and deleted the
        # `from X import (` header - leaving the continuation lines dangling
        # and the module unparseable. It broke features/build.py, and neither
        # pytest nor the golden test caught it because neither imports that
        # module. The command sweep did.
        if line.rstrip().endswith("(") or line.count("(") > line.count(")"):
            print(f"  ? {path}:{ln} multi-line import, left alone "
                  f"(remove '{name}' by hand)")
            continue
        # the only name on the line -> drop the line
        names = re.findall(r"[\w.]+(?:\s+as\s+\w+)?", line.split("import", 1)[-1])
        names = [n for n in names if n not in ("as",)]
        if len(names) <= 1:
            drop_lines.add(ln - 1)
            edits += 1
            continue
        new = re.sub(rf",\s*[\w.]*\b{re.escape(name)}\b(?:\s+as\s+\w+)?", "",
                     line)
        if new == line:
            new = re.sub(rf"\b[\w.]*{re.escape(name)}\b(?:\s+as\s+\w+)?\s*,\s*",
                         "", line)
        if new == line:
            print(f"  ? {path}:{ln} could not remove '{name}', left alone")
            continue
        lines[ln - 1] = new
        edits += 1
    if apply and (drop_lines or edits):
        out = [l for i, l in enumerate(lines) if i not in drop_lines]
        path.write_text("".join(out), encoding="utf-8")
    return edits


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    found = findings()
    total = 0
    for f, items in sorted(found.items()):
        print(f"{f}: {len(items)} unused")
        total += strip(ROOT / f, items, a.apply)
    print(f"\n{total} import(s) {'removed' if a.apply else 'would be removed'}"
          f" across {len(found)} file(s)")
    print("audit.py skipped: its in-function imports ARE the check.")
