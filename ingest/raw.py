"""Keep the raw API response, gzipped, under data/raw/<source>/<date>/.

Everything downstream is a lossy read of these. When a number looks wrong the
question is always "did the feed say that, or did we parse it wrong", and
without the original there is no way to answer. They are small gzipped and
data/ is already excluded from git.

Best-effort by design: a failure here must never cost a pull. The prices are
the thing that cannot be collected again.
"""
import datetime as dt
import gzip
import os
from pathlib import Path

RAW = Path(__file__).parent.parent / "data" / "raw"
KEEP_DAYS = 45


def save_raw(source: str, sport: str, text: str) -> Path | None:
    try:
        day = dt.date.today().isoformat()
        out = RAW / source / day
        out.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%H%M%S")
        path = out / f"{sport}-{stamp}.json.gz"
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(text)
        _prune()
        return path
    except Exception:
        return None                      # never let archiving cost a pull


def _prune(keep_days: int = KEEP_DAYS):
    """Drop day folders older than keep_days. These are for debugging a recent
    surprise, not a permanent archive - archive/ already holds the prices."""
    if not RAW.exists():
        return
    cutoff = dt.date.today() - dt.timedelta(days=keep_days)
    for src in RAW.iterdir():
        if not src.is_dir():
            continue
        for day in src.iterdir():
            try:
                if dt.date.fromisoformat(day.name) < cutoff:
                    for f in day.iterdir():
                        f.unlink()
                    day.rmdir()
            except (ValueError, OSError):
                continue
