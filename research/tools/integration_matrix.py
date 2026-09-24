"""1.5a The integration matrix: one row per entry point, every cell filled.

    python research/tools/integration_matrix.py > docs/integration-matrix.md
    python research/tools/integration_matrix.py --check     # CI guard

WHY A SCRIPT. The last ~45 commits added tables, columns, modes, jobs and docs
on top of a system whose other parts were written before them. A hand-written
matrix would be right on the day it was written and wrong a week later. This
one is regenerated, so drift shows up as a diff.

WHAT IT DOES. For every entry point - each `run_daily.py` mode, each top-level
script, both `.bat` files, both workflows - it resolves the modules that entry
point can reach through imports, scans their SQL for the tables they read and
write, and looks for the entry point's name in the docs, the tests and the
audit.

HOW THE TABLES ARE FOUND. By reading SQL strings out of the source with
regexes, following imports one level at a time until the set stops growing.
This is static: it reports what a path CAN touch, not what it did on a given
night. That is the right question for a matrix whose job is to catch a consumer
nobody updated.

--check exits non-zero when an entry point is undocumented. Phase 2 and 3 add
modes, and the brief asks that adding one without documenting it fails.
"""
import argparse
import ast
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
SKIP_DIRS = {".git", "__pycache__", ".venv", "data", "data_phase1", "archive",
             "logs"}

DOCS = ["COMMANDS.md", "README.md", "CLAUDE.md",
        "docs/state-of-the-machine.md", "docs/production-setup.md"]

# Tables, from db.py's CREATE statements, so the list cannot drift from schema.
def known_tables() -> list:
    src = (ROOT / "db.py").read_text(encoding="utf-8")
    return sorted(set(re.findall(
        r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+(\w+)", src, re.I)))


WRITE_SQL = re.compile(
    r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM|REPLACE\s+INTO)"
    r"\s+[\"'\[]?(\w+)", re.I)
READ_SQL = re.compile(r"\bFROM\s+[\"'\[]?(\w+)|\bJOIN\s+[\"'\[]?(\w+)", re.I)


def py_files():
    for p in sorted(ROOT.rglob("*.py")):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        yield p


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT)).replace("\\", "/")


def mod_name(p: Path) -> str:
    return rel(p)[:-3].replace("/", ".")


def scan():
    """module -> {reads, writes, imports}."""
    tables = set(known_tables())
    info = {}
    for p in py_files():
        try:
            src = p.read_text(encoding="utf-8")
        except Exception:
            continue
        reads, writes = set(), set()
        for m in WRITE_SQL.finditer(src):
            if m.group(1) in tables:
                writes.add(m.group(1))
        for m in READ_SQL.finditer(src):
            for g in m.groups():
                if g and g in tables:
                    reads.add(g)
        imports = set()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            tree = None
        if tree:
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module)
                elif isinstance(node, ast.Import):
                    for a in node.names:
                        imports.add(a.name)
        info[mod_name(p)] = {"reads": reads, "writes": writes,
                             "imports": imports, "file": rel(p)}
    return info


def reachable(info: dict, start: str) -> set:
    """Every local module `start` can reach. Fixed point over imports."""
    seen, frontier = set(), {start}
    while frontier:
        nxt = set()
        for m in frontier:
            if m in seen or m not in info:
                continue
            seen.add(m)
            for imp in info[m]["imports"]:
                if imp in info:
                    nxt.add(imp)
                # `from bets.log import x` when bets/log.py exists
                for cand in (imp, imp.rsplit(".", 1)[0]):
                    if cand in info:
                        nxt.add(cand)
        frontier = nxt - seen
    return seen


def run_modes() -> dict:
    """mode -> the module it dispatches into."""
    src = (ROOT / "run_daily.py").read_text(encoding="utf-8")
    q = "[\"']"
    modes = {}
    for m in re.finditer(r"mode\s*==\s*" + q + r"(\w+)" + q
                         + r"[\s\S]{0,200}?from\s+([\w.]+)\s+import", src):
        modes[m.group(1)] = m.group(2)
    tail = src[src.rfind("{"):]
    for m in re.finditer(q + r"(\w+)" + q + r"\s*:\s*(\w+)", tail):
        modes.setdefault(m.group(1), "run_daily")
    return dict(sorted(modes.items()))


def mode_modules(info: dict, mode: str, target: str) -> set:
    """Only what THIS mode reaches, not everything run_daily.py imports.

    Resolving each mode to `reachable(run_daily)` made every row identical -
    eleven modes with the same six tables - which is exactly useless for
    spotting a consumer nobody updated. A mode is its dispatch function: the
    calls inside that function body, plus any import made inside it, plus the
    module named in its `if mode ==` branch.
    """
    mods = set()
    if target and target != "run_daily":
        mods |= reachable(info, target)
    src = (ROOT / "run_daily.py").read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return mods or reachable(info, "run_daily")

    fname = {"picks": "show_picks"}.get(mode, mode)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == fname), None)
    if fn is None:
        return mods or reachable(info, "run_daily")

    # module-level aliases run_daily binds, e.g. `from bets import log as betlog`
    alias = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            for a in n.names:
                alias[a.asname or a.name] = f"{n.module}.{a.name}"
    for n in ast.walk(fn):
        if isinstance(n, ast.ImportFrom) and n.module:
            mods |= reachable(info, n.module)
        elif isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
            full = alias.get(n.value.id)
            if full:
                for cand in (full, full.rsplit(".", 1)[0]):
                    if cand in info:
                        mods |= reachable(info, cand)
        elif isinstance(n, ast.Name) and n.id in alias:
            full = alias[n.id]
            for cand in (full, full.rsplit(".", 1)[0]):
                if cand in info:
                    mods |= reachable(info, cand)
    return mods or reachable(info, "run_daily")


def entry_points(info: dict) -> list:
    out = []
    for mode, target in run_modes().items():
        out.append({"kind": "run_daily mode", "name": mode,
                    "calls": f"run_daily.py {mode}",
                    "mods": mode_modules(info, mode, target)})
    for p in sorted(ROOT.glob("*.py")):
        name = p.stem
        if name in ("run_daily", "config", "paths"):
            continue
        src = p.read_text(encoding="utf-8")
        if '__main__' not in src:
            continue
        out.append({"kind": "script", "name": name,
                    "calls": f"python {p.name}",
                    "mods": reachable(info, mod_name(p))})
    for p in sorted(ROOT.glob("*.bat")):
        body = p.read_text(encoding="utf-8", errors="replace")
        called = re.findall(r"python\s+([\w\\/]+\.py)(?:\s+(\w+))?", body)
        mods = set()
        for f, mode in called:
            m = f.replace("\\", ".").replace("/", ".")[:-3]
            mods |= reachable(info, m)
        out.append({"kind": "bat", "name": p.name,
                    "calls": ", ".join(f"{f} {m}".strip() for f, m in called)
                             or "none",
                    "mods": mods})
    wf = ROOT / ".github" / "workflows"
    for p in sorted(wf.glob("*.yml")) if wf.exists() else []:
        body = p.read_text(encoding="utf-8")
        called = re.findall(r"python\s+([\w/]+\.py)(?:\s+(\S+))?", body)
        mods = set()
        for f, mode in called:
            mods |= reachable(info, f.replace("/", ".")[:-3])
        out.append({"kind": "workflow", "name": p.name,
                    "calls": ", ".join(sorted({f for f, _ in called})) or "none",
                    "mods": mods})
    return out


def doc_hits(name: str, docs: dict) -> list:
    return [d for d, text in docs.items()
            if re.search(rf"\b{re.escape(name)}\b", text)]


def covered_by(name: str, mods: set) -> tuple:
    """(tests, audit) that mention this entry point or its modules."""
    tests, audit = set(), set()
    for p in sorted((ROOT / "tests").rglob("test_*.py")):
        t = p.read_text(encoding="utf-8")
        if re.search(rf"\b{re.escape(name)}\b", t) or any(
                m.split(".")[-1] in t for m in mods if "." in m or m):
            tests.add(p.name)
    a = (ROOT / "audit.py").read_text(encoding="utf-8")
    for m in mods:
        short = m.split(".")[-1]
        if re.search(rf"\b{re.escape(short)}\b", a):
            audit.add(short)
    return sorted(tests), sorted(audit)


def build():
    info = scan()
    docs = {}
    for d in DOCS:
        p = ROOT / d
        docs[d] = p.read_text(encoding="utf-8") if p.exists() else ""
    rows = []
    for ep in entry_points(info):
        reads, writes = set(), set()
        for m in ep["mods"]:
            reads |= info[m]["reads"]
            writes |= info[m]["writes"]
        tests, audit = covered_by(ep["name"], ep["mods"])
        rows.append({
            "kind": ep["kind"], "name": ep["name"], "calls": ep["calls"],
            "reads": sorted(reads - writes), "writes": sorted(writes),
            "docs": doc_hits(ep["name"], docs),
            "tests": tests[:3], "audit": len(audit),
        })
    return rows


def cell(xs, empty="none"):
    return ", ".join(f"`{x}`" for x in xs) if xs else f"**{empty}**"


def main(check: bool) -> int:
    rows = build()
    undocumented = [r for r in rows if not r["docs"]]
    if check:
        for r in undocumented:
            print(f"UNDOCUMENTED entry point: {r['kind']} {r['name']}")
        print(f"{len(rows)} entry points, {len(undocumented)} undocumented")
        return 1 if undocumented else 0

    print("# Integration matrix\n")
    print("Generated by `research/tools/integration_matrix.py`. One row per "
          "entry point; every cell filled or explicitly **none**.\n")
    print("Tables are found statically, by reading SQL out of every module an "
          "entry point can reach through imports. It reports what a path "
          "**can** touch, not what it did on one night - which is the right "
          "question for catching a consumer nobody updated.\n")
    print(f"**{len(rows)} entry points. {len(undocumented)} undocumented.**\n")

    for kind in ("run_daily mode", "script", "bat", "workflow"):
        group = [r for r in rows if r["kind"] == kind]
        if not group:
            continue
        print(f"\n## {kind}\n")
        print("| entry point | invoked as | writes | reads | documented in | "
              "tests | audit |")
        print("|---|---|---|---|---|---|---|")
        for r in sorted(group, key=lambda x: x["name"]):
            print(f"| **{r['name']}** | `{r['calls']}` | {cell(r['writes'])} "
                  f"| {cell(r['reads'])} | {cell(r['docs'])} "
                  f"| {cell(r['tests'])} | {r['audit'] or '**none**'} |")

    if undocumented:
        print("\n## Undocumented\n")
        for r in undocumented:
            print(f"- **{r['name']}** ({r['kind']}) — `{r['calls']}`")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    raise SystemExit(main(ap.parse_args().check))
