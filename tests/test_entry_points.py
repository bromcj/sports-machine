"""Every entry point is documented, and every module is reachable from one.

This replaces research/tools/integration_matrix.py, which was a report rather
than a check: it missed the `backup` mode, could not follow
`from ingest import quality`, counted a mode as documented if its name was any
word in five docs, had no unreachable-module check, and was not run by CI.

Found by parsing, not by a hand-kept list:
  modes        every `mode == "x"` branch and dispatch-dict key in run_daily.py
  entry points run_daily.py, every .py a .bat or workflow runs, and every
               `python x.py` COMMANDS.md tells the owner to run
  reachable    the transitive imports of those (including imports inside
               functions and `from pkg import submodule`)
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
COMMANDS = (ROOT / "COMMANDS.md").read_text(encoding="utf-8")

# Kept although nothing schedules them, because each reproduces a recorded
# result: the doc named here cites the script by name. Their imports count as
# reachable. Anything else unreachable is a sweep miss.
REFERENCE = {
    "research/part_e_model.py": "docs/part-e-results.md",
    "research/part_e_prices.py": "docs/part-e-results.md",
    "research/enrich_statcast.py": "docs/part-e-results.md",
    "research/a1_movement.py": "docs/experiments.md",
    "research/d1_calibration.py": "docs/decisions.md",
    "research/b3/materialize.py": "docs/b3-results.md",
    "props/nfl_receiving.py": "docs/b3-results.md",
    "features/build_training_nfl.py": "README.md",
}


def _modes() -> set:
    tree = ast.parse((ROOT / "run_daily.py").read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Compare) and isinstance(node.left, ast.Name)
                and node.left.id == "mode"):
            for c in node.comparators:
                if isinstance(c, ast.Constant) and isinstance(c.value, str):
                    found.add(c.value)
                if isinstance(c, (ast.Tuple, ast.List, ast.Set)):
                    found |= {e.value for e in c.elts if isinstance(e, ast.Constant)}
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Dict)
                and isinstance(node.slice, ast.Name) and node.slice.id == "mode"):
            found |= {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    return found


def test_every_mode_is_dispatched_and_listed():
    import run_daily
    assert _modes() == set(run_daily.MODES)


def test_every_mode_is_documented_as_a_command():
    missing = [m for m in _modes()
               if not re.search(rf"run_daily\.py\s+{re.escape(m)}\b", COMMANDS)]
    assert not missing, f"undocumented run_daily modes: {missing}"


def _module_file(name: str):
    p = ROOT.joinpath(*name.split("."))
    for cand in (p.with_suffix(".py"), p / "__init__.py"):
        if cand.exists():
            return cand
    return None


def _imports(path: Path) -> set:
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out |= {f"{node.module}.{a.name}" for a in node.names}
    return out


def _entry_points() -> set:
    files = {ROOT / "run_daily.py"}
    texts = [p.read_text(encoding="utf-8") for p in ROOT.glob("*.bat")]
    texts += [p.read_text(encoding="utf-8")
              for p in (ROOT / ".github" / "workflows").glob("*.yml")]
    texts.append(COMMANDS)
    for t in texts:
        for rel in re.findall(r"([\w/\\]+\.py)\b", t):
            p = ROOT / rel.replace("\\", "/")
            if p.exists() and "tests" not in p.parts:
                files.add(p)
    return files


def _reachable() -> set:
    seen, todo = set(), list(_entry_points() | {ROOT / r for r in REFERENCE})
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        for name in _imports(f):
            parts = name.split(".")
            for i in range(1, len(parts) + 1):   # a.b.c also loads a and a.b
                m = _module_file(".".join(parts[:i]))
                if m and m not in seen:
                    todo.append(m)
    return seen


def test_every_module_is_reachable_or_a_named_reference():
    reach = _reachable()
    orphans = []
    for p in sorted(ROOT.rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        if rel.split("/")[0] in ("tests", "data", "data_golden", "data_phase1"):
            continue
        if p.name == "__init__.py" or p in reach:
            continue
        orphans.append(rel)
    assert not orphans, ("unreachable from any entry point, and not a named "
                         f"reference: {orphans}")


def test_every_reference_cites_a_doc_that_exists_and_names_it():
    for rel, doc in REFERENCE.items():
        assert (ROOT / rel).exists(), rel
        text = (ROOT / doc).read_text(encoding="utf-8")
        assert Path(rel).name in text or Path(rel).stem in text, (rel, doc)
