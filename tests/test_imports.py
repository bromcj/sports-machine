"""Every module parses and imports. Seconds, no database, no network.

`audit.py` already checks this, and it caught the failure that prompted this
file - but audit takes minutes, so in practice it runs at the end of a batch of
changes rather than before each commit. A cleanup that removes an import can
leave a module unparseable, and neither the rest of the suite nor the golden
test will notice if nothing they touch imports it.

That is not hypothetical. An automated unused-import sweep deleted the header
of a parenthesised multi-line import in `features/build.py`, leaving the
continuation lines dangling. 147 tests passed and the golden test reported
"nothing moved", because neither imports that module. The command sweep found
it; this finds it in two seconds.
"""
import ast
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

SKIP_PARTS = {"__pycache__", ".venv", "archive", "logs", "node_modules"}
SKIP_PREFIXES = ("data",)      # data/, data_phase1/, any local copy


def _modules():
    for p in sorted(ROOT.rglob("*.py")):
        rel = p.relative_to(ROOT)
        if any(part in SKIP_PARTS for part in rel.parts):
            continue
        if rel.parts[0].startswith(SKIP_PREFIXES):
            continue
        yield rel


@pytest.mark.parametrize("rel", list(_modules()), ids=str)
def test_module_parses(rel):
    src = (ROOT / rel).read_text(encoding="utf-8")
    try:
        ast.parse(src)
    except SyntaxError as e:
        pytest.fail(f"{rel}:{e.lineno} {e.msg}\n  {(e.text or '').rstrip()}")


# Importing runs module-level code. Entry points guard it behind __main__, so
# this is safe - and a module that does real work at import time is itself
# worth finding.
IMPORTABLE = [r for r in _modules()
              if r.name != "__init__.py" and r.parts[0] != "tests"]


@pytest.mark.parametrize("rel", IMPORTABLE, ids=str)
def test_module_imports(rel):
    name = ".".join(rel.parts)[:-3]
    try:
        importlib.import_module(name)
    except Exception as e:                      # noqa: BLE001 - report anything
        pytest.fail(f"{name}: {type(e).__name__}: {e}")
