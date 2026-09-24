"""Fit a model on all available history and save it, so today's games can be
scored without retraining.

walk_forward() measures models and throws them away - that is its job, and the
numbers it reports are honest precisely because each one only ever saw the past.
But nothing was left on disk afterwards, so there was no way to score a game
that has not been played.

This fits ONE final model on every season available and saves it alongside the
things a prediction needs to be interpretable later: which features it expects
and in what order, how many rows it saw, the last date in its training data,
and the margin->probability k. Predicting with a feature vector in the wrong
order fails silently and produces confident nonsense, so the column list
travels with the model rather than being re-derived at prediction time.
"""
import datetime as dt
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import sys as _sys
_sys.path.insert(0, str(Path(__file__).parent.parent))
import paths
from config import SPORTS

MODEL_DIR = paths.MODELS_DIR
FORMAT_VERSION = 1


def model_path(sport: str) -> Path:
    return MODEL_DIR / f"{sport}.joblib"


def save(sport: str, df: pd.DataFrame, feature_cols: list[str],
         target_col: str, alpha: float, k: float | None = None,
         date_col: str = "game_date", a: float = 0.0) -> Path:
    """Fit on everything in df and write the bundle to data/models/<sport>.joblib.

    `a` is the home intercept (docs/calibration-3.6.md / D1). It travels in the
    bundle rather than being looked up at prediction time, for the same reason
    feature_cols does: a saved model has to carry everything needed to
    reproduce its own output, or a later config edit silently reprices every
    game the old model scored.
    """
    model = make_pipeline(StandardScaler(), Ridge(alpha=alpha))
    model.fit(df[feature_cols], df[target_col])

    trained_through = None
    if date_col in df.columns:
        trained_through = str(pd.to_datetime(df[date_col]).max().date())

    bundle = {
        "format_version": FORMAT_VERSION,
        "sport": sport,
        "model": model,
        "feature_cols": list(feature_cols),   # ORDER MATTERS at predict time
        "target_col": target_col,
        "alpha": alpha,
        "k": float(k if k is not None else SPORTS[sport]["k_default"]),
        "a": float(a),
        "n_train_rows": int(len(df)),
        "seasons": sorted(int(s) for s in df["season"].unique()) if "season" in df else [],
        "trained_through": trained_through,
        "trained_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, model_path(sport))
    # Register it HERE, not only when it first predicts. A retrain
    # writes a new file with a new hash, and between the retrain and
    # the next prediction the saved model was a stranger to its own
    # lineage table.
    try:
        from db import connect as _c
        _con = _c()
        register_model(_con, sport, bundle)
        _con.close()
    except Exception:
        pass          # never let bookkeeping lose a trained model
    return model_path(sport)


def load(sport: str) -> dict:
    p = model_path(sport)
    if not p.exists():
        raise FileNotFoundError(
            f"No saved model at {p}. Run the sport's build_training script first.")
    bundle = joblib.load(p)
    if bundle.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            f"{p} is format {bundle.get('format_version')}, expected "
            f"{FORMAT_VERSION}. Re-run the training script.")
    return bundle


def predict_margin(bundle: dict, features: pd.DataFrame) -> np.ndarray:
    """Project margin for pre-built feature rows, in the model's column order.

    Raises rather than guessing if a column is missing: a silently-zeroed
    feature is worse than a crash, because it produces a confident wrong price.
    """
    missing = [c for c in bundle["feature_cols"] if c not in features.columns]
    if missing:
        raise KeyError(f"feature vector is missing {missing}")
    return bundle["model"].predict(features[bundle["feature_cols"]])


def win_prob(bundle: dict, margins: np.ndarray) -> np.ndarray:
    """sigmoid(a + k*margin), with both read from the bundle.

    .get("a", 0.0) rather than ["a"]: a bundle written before D1 has no `a`,
    and the right behaviour for one of those is the behaviour it was scored
    with, not a crash and not a silent new number.
    """
    a = float(bundle.get("a", 0.0))
    return 1.0 / (1.0 + np.exp(-(a + bundle["k"] * np.asarray(margins))))


def describe(sport: str) -> str:
    try:
        b = load(sport)
    except (FileNotFoundError, ValueError) as e:
        return f"{sport}: {e}"
    return (f"{sport}: ridge(alpha={b['alpha']}) k={b['k']} "
            f"a={b.get('a', 0.0):+.4f} | "
            f"{b['n_train_rows']} rows, seasons {b['seasons']} | "
            f"trained through {b['trained_through']} | "
            f"{len(b['feature_cols'])} features")


if __name__ == "__main__":
    for sp in ("mlb", "nfl"):
        print(describe(sp))


def model_id(sport: str = "mlb") -> str | None:
    """sha256 of the saved model file. Two fits on the same data differ here.

    model_version was "ridge-<sport>-<trained_through>", which is identical for
    two different fits over the same seasons - so it could not identify which
    model made a prediction. The file's own hash can.
    """
    import hashlib
    path = MODEL_DIR / f"{sport}.joblib"
    if not path.exists():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def register_model(con, sport: str, bundle: dict) -> str | None:
    """Record this model in `models` if it is not already there. Returns its id."""
    import json as _json
    from db import code_sha, utc_now
    mid = model_id(sport)
    if mid is None:
        return None
    if con.execute("SELECT 1 FROM models WHERE model_id=?", (mid,)).fetchone():
        return mid
    con.execute(
        "INSERT INTO models (model_id, sport, trained_at, code_sha,"
        " data_through, alpha, k, n_train_rows, metrics_json)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (mid, sport, bundle.get("trained_at") or utc_now(), code_sha(),
         bundle.get("trained_through"), bundle.get("alpha"), bundle.get("k"),
         bundle.get("n_train_rows"),
         _json.dumps({k: v for k, v in bundle.items()
                      if isinstance(v, (int, float, str, type(None)))})))
    con.commit()
    return mid
