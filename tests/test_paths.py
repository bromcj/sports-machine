"""One definition of where the data lives, overridable by one variable."""
import importlib
from pathlib import Path


def test_the_default_is_the_repo_data_folder(monkeypatch):
    monkeypatch.delenv("SPORTS_MACHINE_DATA_DIR", raising=False)
    import paths
    p = importlib.reload(paths)
    assert p.DATA_DIR == p.ROOT / "data"
    assert "default" in p.describe()


def test_the_environment_variable_moves_everything(monkeypatch, tmp_path):
    monkeypatch.setenv("SPORTS_MACHINE_DATA_DIR", str(tmp_path))
    import paths
    p = importlib.reload(paths)
    assert p.DATA_DIR == tmp_path.resolve()
    # Every derived path must follow, or a second checkout half-moves.
    for child in (p.DB_PATH, p.STATCAST_DIR, p.MODELS_DIR, p.NFL_DIR, p.RAW_DIR,
                  p.training_table("mlb")):
        assert tmp_path.resolve() in child.parents or child.parent == tmp_path.resolve()
    monkeypatch.delenv("SPORTS_MACHINE_DATA_DIR")
    importlib.reload(p)


def test_no_module_computes_its_own_data_path():
    """The thing that made a second checkout impossible."""
    root = Path(__file__).parent.parent
    offenders = []
    for py in root.rglob("*.py"):
        rel = py.relative_to(root)
        if rel.parts[0] in ("tests", "archive", ".venv", "__pycache__"):
            continue
        if py.name == "paths.py":
            continue
        text = py.read_text(encoding="utf-8", errors="ignore")
        if '/ "data"' in text or '"data/' in text:
            offenders.append(str(rel))
    assert not offenders, f"still building their own data path: {offenders}"
