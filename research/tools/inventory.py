"""1.1 Inventory: count the bloat before touching any of it. Free, no data.

    python research/tools/inventory.py > docs/cleanup-inventory.md

A script rather than a hand-written list, so it can be re-run after the cleanup
and the before/after is a diff rather than a claim. It parses the source with
`ast`; it does not import anything, so it cannot be confused by a module that
happens to have side effects at import time.

What it measures, in the order 1.1 asks for: dead code, duplication, prose,
long functions, layout, audit-vs-test overlap, doc drift.
"""
import ast
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
SKIP_DIRS = {".git", "__pycache__", ".venv", "data", "data_phase1", "archive",
             "logs", "node_modules"}

# A calculation has ONE canonical home. What matters is not how many files
# call it - that is the point of having one - but how many compute it again
# for themselves. So each entry names the canonical function, the file that is
# allowed to define it, and the shapes an INLINE RE-IMPLEMENTATION takes.
#
# The first version of this counted call sites and reported 61 "duplicates" of
# fair_prob, every one of which was a file correctly calling the one
# implementation. Counting usage as duplication would have sent the cleanup
# after exactly the wrong thing.
CANONICAL = {
    "odds -> probability": {
        "fn": "american_to_prob", "home": "bets/engine.py",
        "inline": [r"100\s*/\s*\(\s*\w*ml\w*\s*\+\s*100",
                   r"abs\(\s*\w*ml\w*\s*\)\s*/\s*\(\s*abs",
                   r"-\s*\w*ml\w*\s*/\s*\(\s*-\s*\w*ml\w*\s*\+\s*100"],
    },
    "de-vig": {
        "fn": "novig_probs", "home": "bets/engine.py",
        "inline": [r"/\s*\(\s*pa\s*\+\s*ph\s*\)", r"\bpa\s*/\s*total\b",
                   r"\bph\s*/\s*total\b", r"tot\s*-\s*1\.0"],
    },
    "UTC parsing": {
        "fn": "parse_utc", "home": "feeds.py",
        "inline": [r'replace\(\s*["\']Z["\']\s*,\s*["\']\+00:00'],
    },
    "twin matching": {
        "fn": "odds_twin", "home": "feeds.py",
        "inline": [r"\(date,\s*away,\s*home\)",
                   r"game_date\s*==.*away\s*==.*home\s*=="],
    },
    "latest pregame snapshot": {
        "fn": "closing_snapshot", "home": "bets/log.py",
        "inline": [r"commence_time.*>\s*ts", r"max\(.*ts.*commence"],
    },
    "status mapping": {
        "fn": "stats_api_status", "home": "feeds.py",
        "inline": [r"abstractGameState", r"detailedState"],
    },
    "fair probability": {
        "fn": "fair_prob", "home": "bets/log.py",
        "inline": [r"p_fair_sharp.*where", r"pin\.where\("],
    },
}

HISTORY_WORDS = re.compile(
    r"\b(used to|previously|the previous version|it used to|once |formerly|"
    r"measured (on|in) 20\d\d|before this|this used to|turned out)\b", re.I)


def py_files():
    for p in sorted(ROOT.rglob("*.py")):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        yield p


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT)).replace("\\", "/")


def parse(p: Path):
    try:
        return ast.parse(p.read_text(encoding="utf-8")), p.read_text(
            encoding="utf-8").splitlines()
    except Exception:
        return None, []


# ------------------------------------------------------------------ dead ---

def imports_and_defs():
    imported = defaultdict(set)      # module -> set of files importing it
    defined = {}                     # "mod:func" -> (file, lineno, nlines)
    called = set()
    for p in py_files():
        tree, lines = parse(p)
        if tree is None:
            continue
        mod = rel(p)[:-3].replace("/", ".")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imported[a.name].add(rel(p))
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported[node.module].add(rel(p))
                for a in node.names:
                    called.add(a.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end = getattr(node, "end_lineno", node.lineno)
                defined[f"{mod}:{node.name}"] = (rel(p), node.lineno,
                                                 end - node.lineno + 1)
            elif isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name):
                    called.add(f.id)
                elif isinstance(f, ast.Attribute):
                    called.add(f.attr)
    return imported, defined, called


def unused_imports():
    out = []
    for p in py_files():
        tree, lines = parse(p)
        if tree is None:
            continue
        src = "\n".join(lines)
        names = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    if a.name == "*":
                        continue
                    nm = a.asname or a.name.split(".")[0]
                    names.append((nm, node.lineno))
        for nm, ln in names:
            # crude but honest: count uses outside the import line itself
            uses = len(re.findall(rf"\b{re.escape(nm)}\b", src))
            if uses <= 1:
                out.append((rel(p), ln, nm))
    return out


# ------------------------------------------------------------- duplication --

def duplication():
    """For each calculation: where it lives, who calls it, who redoes it.

    Only the third column is duplication. A call site is the system working.
    """
    out = {}
    for label, spec in CANONICAL.items():
        callers, inline = set(), []
        for p in py_files():
            f = rel(p)
            try:
                lines = p.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue
            for i, line in enumerate(lines, 1):
                s = line.strip()
                if s.startswith("#") or not s:
                    continue
                if spec["fn"] in s and f != spec["home"]:
                    callers.add(f)
                # Not duplication: the canonical file itself; a line that
                # CALLS the canonical function (the regex often matches its
                # arguments); a test or audit fixture, which legitimately
                # constructs the input shape; and this file, which contains
                # the patterns as data.
                if (f == spec["home"] or spec["fn"] in s
                        or f.startswith(("tests/", "research/tools/"))):
                    continue
                for pat in spec["inline"]:
                    if re.search(pat, s):
                        inline.append((f, i, s[:72]))
                        break
        out[label] = {"home": spec["home"], "fn": spec["fn"],
                      "callers": sorted(callers), "inline": inline}
    return out


# ------------------------------------------------------------------ prose ---

def prose():
    rows = []
    for p in py_files():
        tree, lines = parse(p)
        if tree is None:
            continue
        total = len(lines)
        comment = sum(1 for l in lines if l.strip().startswith("#"))
        doc = 0
        long_docs = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
                d = ast.get_docstring(node)
                if d:
                    n = len(d.splitlines())
                    doc += n
                    if n > 15:
                        name = getattr(node, "name", "<module>")
                        long_docs.append((name, n))
        blank = sum(1 for l in lines if not l.strip())
        code = total - comment - doc - blank
        rows.append({"file": rel(p), "total": total, "code": max(code, 0),
                     "comment": comment, "doc": doc,
                     "prose_pct": round(100 * (comment + doc) / max(total, 1), 1),
                     "long_docstrings": long_docs})
    return rows


def history_comments():
    out = []
    for p in py_files():
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines, 1):
            if HISTORY_WORDS.search(line):
                out.append((rel(p), i, line.strip()[:90]))
    return out


# --------------------------------------------------------- long functions --

def long_functions(threshold=60):
    out = []
    for p in py_files():
        tree, _ = parse(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end = getattr(node, "end_lineno", node.lineno)
                n = end - node.lineno + 1
                if n >= threshold:
                    out.append((rel(p), node.name, node.lineno, n))
    return sorted(out, key=lambda r: -r[3])


# ----------------------------------------------------------------- layout --

def run_modes():
    """Every mode run_daily.py dispatches. Both shapes: `if mode ==` and the
    trailing dict. The first version of this caught only four of eleven."""
    rd = ROOT / "run_daily.py"
    if not rd.exists():
        return []
    src = rd.read_text(encoding="utf-8")
    q = "[\"']"
    modes = set(re.findall(r"mode\s*==\s*" + q + r"(\w+)" + q, src))
    tail = src[src.rfind("{"):] if "{" in src else ""
    modes |= set(re.findall(q + r"(\w+)" + q + r"\s*:", tail))
    return sorted(modes)


def layout():
    top_py = sorted(p.name for p in ROOT.glob("*.py"))
    top_md = sorted(p.name for p in ROOT.glob("*.md"))
    top_bat = sorted(p.name for p in ROOT.glob("*.bat"))
    return top_py, top_md, top_bat


# ------------------------------------------------------------------- main --

def main():
    imported, defined, called = imports_and_defs()
    print("# Cleanup inventory\n")
    print("Generated by `research/tools/inventory.py`. Numbers, not opinions; "
          "re-run it after the cleanup and the difference is a diff.\n")

    # --- 1 dead code
    print("## 1. Dead and orphaned code\n")
    suspects = ["features.registry", "resolve_market_close",
                "export_snapshots", "props.b3_dryrun", "props.check_thursday",
                "props.preflight", "research.a1_movement"]
    print("| module | files importing it |")
    print("|---|---|")
    for m in suspects:
        who = sorted(imported.get(m, set()) | imported.get(m.split(".")[-1], set()))
        print(f"| `{m}` | {', '.join(who) if who else '**none**'} |")
    never = [k for k, v in defined.items()
             if k.split(":")[1] not in called
             and not k.split(":")[1].startswith("_")
             and k.split(":")[1] not in ("main", "build", "run")]
    print(f"\nFunctions defined and never called anywhere in the tree: "
          f"**{len(never)}**\n")
    for k in sorted(never)[:25]:
        f, ln, n = defined[k]
        print(f"- `{k.split(':')[1]}` — {f}:{ln} ({n} lines)")
    ui = unused_imports()
    print(f"\nProbably-unused imports: **{len(ui)}** across "
          f"{len({r[0] for r in ui})} files\n")
    for f, ln, nm in ui[:20]:
        print(f"- {f}:{ln} `{nm}`")

    # --- 2 duplication
    print("\n## 2. Duplication\n")
    print("A call site is the system working; only an inline "
          "re-implementation is duplication.\n")
    dup = duplication()
    print("| calculation | canonical home | callers | RE-IMPLEMENTATIONS |")
    print("|---|---|---|---|")
    for label, d in dup.items():
        print(f"| {label} | `{d['fn']}` in {d['home']} | {len(d['callers'])} "
              f"| **{len(d['inline'])}** |")
    for label, d in dup.items():
        if not d["inline"]:
            continue
        print(f"\n**{label}** — computed again outside {d['home']}:\n")
        for f, ln, txt in d["inline"][:15]:
            print(f"- {f}:{ln} `{txt}`")
        if len(d["inline"]) > 15:
            print(f"- ... and {len(d['inline']) - 15} more")

    # --- 3 prose
    print("\n## 3. Prose\n")
    rows = sorted(prose(), key=lambda r: -r["prose_pct"])
    tot = sum(r["total"] for r in rows)
    pr = sum(r["comment"] + r["doc"] for r in rows)
    print(f"Overall: **{pr:,} of {tot:,} lines are prose "
          f"({100 * pr / max(tot, 1):.1f}%)**. Target under 15%.\n")
    print("| file | lines | code | comment | docstring | prose % |")
    print("|---|---|---|---|---|---|")
    for r in rows[:20]:
        print(f"| {r['file']} | {r['total']} | {r['code']} | {r['comment']} | "
              f"{r['doc']} | {r['prose_pct']} |")
    longd = [(r["file"], n, c) for r in rows for n, c in r["long_docstrings"]]
    print(f"\nDocstrings over 15 lines: **{len(longd)}**\n")
    for f, n, c in sorted(longd, key=lambda x: -x[2])[:20]:
        print(f"- {f} `{n}` — {c} lines")
    hc = history_comments()
    print(f"\nComments narrating history: **{len(hc)}**\n")
    for f, ln, txt in hc[:20]:
        print(f"- {f}:{ln} `{txt}`")

    # --- 4 long functions
    lf = long_functions()
    print(f"\n## 4. Long functions\n\nOver 60 lines: **{len(lf)}**\n")
    print("| file | function | line | length |")
    print("|---|---|---|---|")
    for f, name, ln, n in lf[:30]:
        print(f"| {f} | `{name}` | {ln} | **{n}** |")

    # --- 5 layout
    py, md, bat = layout()
    print(f"\n## 5. Layout\n")
    print(f"- top-level `.py`: **{len(py)}** — {', '.join(py)}")
    print(f"- top-level `.md`: **{len(md)}** — {', '.join(md)}")
    print(f"- top-level `.bat`: **{len(bat)}** — {', '.join(bat)}")
    sp = [rel(p) for p in py_files()
          if "sys.path.insert" in p.read_text(encoding="utf-8")]
    print(f"- files with `sys.path.insert`: **{len(sp)}**")

    # --- 6 audit vs tests
    print("\n## 6. Audit vs tests\n")
    audit = ROOT / "audit.py"
    checks = []
    if audit.exists():
        for i, line in enumerate(audit.read_text(encoding="utf-8").splitlines(), 1):
            m = re.search(r'check\(\s*f?["\'](.+?)["\']', line)
            if m:
                checks.append((i, m.group(1)))
    print(f"`audit.py` check() calls: **{len(checks)}**")
    tests = sorted(ROOT.glob("tests/test_*.py"))
    ntest = 0
    for t in tests:
        ntest += len(re.findall(r"^def test_", t.read_text(encoding="utf-8"),
                                re.M))
    print(f"pytest files: **{len(tests)}**, test functions: **{ntest}**")

    # --- 7 docs
    print("\n## 7. Docs\n")
    for name in ("README.md", "COMMANDS.md", "CLAUDE.md",
                 "docs/state-of-the-machine.md", "docs/production-setup.md"):
        p = ROOT / name
        if p.exists():
            print(f"- {name}: {len(p.read_text(encoding='utf-8').splitlines())} lines")
    print(f"\n`run_daily.py` modes: {', '.join('`%s`' % m for m in run_modes())}")
    print("\n| mode | COMMANDS.md | README.md | state-of-the-machine.md |")
    print("|---|---|---|---|")
    docs = {}
    for name in ("COMMANDS.md", "README.md", "docs/state-of-the-machine.md"):
        p = ROOT / name
        docs[name] = p.read_text(encoding="utf-8") if p.exists() else ""
    for m in run_modes():
        cells = []
        for name in docs:
            hit = re.search(rf"run_daily\.py\s+{m}\b|`{m}`|\b{m}\b", docs[name])
            cells.append("yes" if hit else "**NO**")
        print(f"| `{m}` | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
