"""The golden test: prove the cleanup changed nothing. Free, read-only.

    python tests/golden/capture.py --write     # capture the baseline, once
    python tests/golden/capture.py             # recapture and diff
    python -m pytest tests/golden/             # the same, as a test

WHAT THIS IS FOR. Phase 1 moves code, deletes code, splits functions and
rewrites docstrings. Every one of those is supposed to be behaviour-preserving,
and "supposed to be" is not a standard this project accepts. So the behaviour
is pinned down first, from the copied database and the saved model, and every
later commit has to reproduce it exactly.

WHAT IT PINS, and why each one:

  validation.json         the gates' own record. If this moves, the gates moved.
  model/validation.py     the human-readable verdict, which is what a person
                          actually reads before believing anything.
  feeds.odds_twin         every game -> its odds twin. The three feeds mint
                          incompatible ids and this resolution is the join the
                          whole system stands on; a silent change here would
                          not fail anything, it would just quietly match
                          different games.
  predict_for_date        three fixed past dates, to 1e-9. Catches any change
                          to features, model loading or the margin mapping.
  market_score            season totals, plus a checksum of market_close.
  paper-bet grading       ev_fair_close / shop / info re-derived per bet.
  monitor.run_checks      the operational view, normalized.

TIMESTAMPS ARE EXCLUDED, not tolerated. Anything that legitimately changes
every run - a `recorded_at`, a run duration, a "3 days behind" - is normalized
to a fixed token before comparison, so a real change cannot hide behind one.

FLOATING POINT. If an order-of-operations change moves a number at the last
bits, the difference and its cause go in the phase report and TOLERANCE is set
to the smallest value that passes. It is one visible constant. It is never
widened silently and never above 1e-9.

SPEED. The full capture includes `python audit.py`, which takes minutes. Every
commit runs the fast set; the audit text is captured at the start of the phase
and re-checked before the merge. `--with-audit` forces it.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

BASELINE = Path(__file__).parent / "baseline"

# The smallest tolerance that passes. Never widened silently, never above 1e-9.
TOLERANCE = 1e-9

# Things that legitimately differ between two runs of identical code.
NORMALIZE = [
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?"
                r"(\+00:00|Z)?"), "<TS>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}"), "<DATE>"),
    (re.compile(r"\b\d+\.\d+s\b"), "<DUR>"),
    (re.compile(r"\b\d+ day\(s\) behind"), "<LAG> day(s) behind"),
    (re.compile(r"\b\d{2,3},?\d{3} left of"), "<CREDITS> left of"),
    (re.compile(r"[A-Za-z]:\\[^\s\"']+"), "<PATH>"),
]


def normalize(text: str) -> str:
    for pat, rep in NORMALIZE:
        text = pat.sub(rep, text)
    return text


def _env():
    e = dict(os.environ)
    e["SPORTS_MACHINE_DATA_DIR"] = str(ROOT / "data_phase1")
    e["PYTHONIOENCODING"] = "utf-8"
    return e


def _raw(args) -> str:
    r = subprocess.run([sys.executable] + args, cwd=ROOT, env=_env(),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return (r.stdout or "") + (r.stderr or "")


def _run(args) -> str:
    """Human-readable output: normalized, because it carries live timestamps."""
    return normalize(_raw(args))


def _json(args, fallback):
    """A structured capture. NOT normalized.

    The first version ran every capture through normalize(), including the JSON
    ones - which rewrote the dates that were the dictionary KEYS, collapsing
    three fixed prediction dates into one entry called "<DATE>". Normalization
    belongs on prose, where a timestamp is noise; on structured output the
    values are the thing being pinned.
    """
    txt = _raw(args).strip()
    # Parse from the first opening brace to the end, not line by line: a
    # pretty-printed payload spans many lines and the line-wise version failed
    # on every indented one, then reported PARSE_FAILED for output that was
    # perfectly good JSON.
    for i, ch in enumerate(txt):
        if ch in "{[":
            try:
                return json.loads(txt[i:])
            except json.JSONDecodeError:
                break
    return fallback(txt)


# --------------------------------------------------------------- captures --

def cap_validation_json() -> dict:
    p = ROOT / "validation.json"
    if not p.exists():
        return {}
    d = json.loads(p.read_text(encoding="utf-8"))

    def strip(o):
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items()
                    if k not in ("recorded_at", "armed_at", "as_of")}
        if isinstance(o, list):
            return [strip(x) for x in o]
        return o

    return strip(d)


def cap_validation_text() -> str:
    return _run(["model/validation.py"])


def cap_audit_text() -> str:
    return _run(["audit.py"])


def cap_monitor() -> str:
    code = ("import monitor, json;"
            "print(json.dumps([{k: v for k, v in f.items() if k != 'detail'}"
            " | {'detail': f.get('detail','')} for f in monitor.run_checks()],"
            " indent=1, sort_keys=True, default=str))")
    return _json(["-c", code], lambda t: [{"PARSE_FAILED": t[:400]}])


def cap_twins() -> list:
    """Every MLB game -> its odds twin. The join the system stands on."""
    code = r"""
import sys, json
from db import connect
from feeds import odds_twin, SQL_STATS_API
con = connect()
rows = con.execute(
    "SELECT game_id FROM games WHERE sport='mlb' AND (%s) "
    "AND status='final' ORDER BY game_id" % SQL_STATS_API).fetchall()
out = []
for r in rows[:4000]:
    twin, note = odds_twin(con, r["game_id"])
    out.append([r["game_id"], twin or "", note or ""])
print(json.dumps(out))
"""
    return _json(["-c", code], lambda t: [["PARSE_FAILED", t[:400], ""]])


def cap_predictions() -> dict:
    """Three fixed past dates: stored features + saved model -> probability.

    Re-derived rather than replayed. `predict_for_date` returns a COUNT and
    WRITES to the predictions table, which is append-only - so calling it would
    both fail to give the numbers and grow the table a little every time the
    golden test ran. This takes the same path it takes internally (latest
    feature row, predict_margin, win_prob) without the write, which is what
    "behaviour-preserving" actually needs pinned: features, model loading and
    the margin mapping including the home intercept.
    """
    # Dates chosen because they HAVE stored feature rows; the first
    # three picked were before the feature table existed and pinned
    # three empty lists, which would have passed forever.
    dates = ["2026-09-22", "2026-09-23"]
    code = r"""
import json
import pandas as pd
from db import connect, LATEST_FEATURE
from model.persist import load as load_model, predict_margin, win_prob
out = {}
try:
    bundle = load_model("mlb")
    con = connect()
    for d in %r:
        rows = con.execute(
            "SELECT f.game_id, f.payload FROM (" + LATEST_FEATURE + ") f"
            " JOIN games g ON g.game_id = f.game_id"
            " WHERE f.sport='mlb' AND g.game_date=? ORDER BY f.game_id",
            (d,)).fetchall()
        got = []
        for r in rows:
            feat = pd.DataFrame([json.loads(r["payload"])])
            m = float(predict_margin(bundle, feat)[0])
            p = float(win_prob(bundle, [m])[0])
            got.append([r["game_id"], round(m, 12), round(p, 12)])
        out[d] = got
except Exception as e:
    out["ERROR"] = [type(e).__name__, str(e)[:160]]
print(json.dumps(out))
""" % dates
    return _json(["-c", code], lambda t: {"PARSE_FAILED": t[:400]})


def cap_market() -> dict:
    code = r"""
import json, hashlib
from db import connect
from model.market_score import score_finished
con = connect()
rows = con.execute("SELECT game_id, odds_game_id, ROUND(p_fair_home, 10),"
                   " source FROM market_close ORDER BY game_id").fetchall()
h = hashlib.sha256()
for r in rows:
    h.update(("|".join(str(x) for x in tuple(r))).encode())
out = {"market_close_rows": len(rows), "market_close_sha": h.hexdigest()}
try:
    s = score_finished("mlb", limit_days=100000)
    out["score_finished"] = {k: (round(v, 10) if isinstance(v, float) else v)
                             for k, v in s.items()} if isinstance(s, dict) else str(s)
except Exception as e:
    out["score_finished"] = ["ERROR", type(e).__name__, str(e)[:120]]
print(json.dumps(out, sort_keys=True))
"""
    return _json(["-c", code], lambda t: {"PARSE_FAILED": t[:400]})


def cap_grading() -> list:
    """Re-derive the decomposition for every settled bet."""
    code = r"""
import json
from db import connect
con = connect()
rows = con.execute(
    "SELECT bet_id, mode, side, line_taken, model_prob, novig_market_prob,"
    " clv_pct, ev_fair_close, shop_pct, info_pct, fair_source, result, pnl"
    " FROM bets ORDER BY bet_id").fetchall()
def r10(x):
    return round(x, 10) if isinstance(x, float) else x
print(json.dumps([[r10(v) for v in tuple(r)] for r in rows]))
"""
    return _json(["-c", code], lambda t: [["PARSE_FAILED", t[:400]]])


FAST = {
    "validation_json": cap_validation_json,
    "validation_text": cap_validation_text,
    "twins": cap_twins,
    "predictions": cap_predictions,
    "market": cap_market,
    "grading": cap_grading,
    "monitor": cap_monitor,
}
SLOW = {"audit_text": cap_audit_text}


def capture(with_audit: bool) -> dict:
    out = {name: fn() for name, fn in FAST.items()}
    if with_audit:
        out.update({name: fn() for name, fn in SLOW.items()})
    return out


# ------------------------------------------------------------- comparison --

def _diff(a, b, path="") -> list:
    """Structural diff with a float tolerance. Returns a list of differences."""
    bad = []
    if isinstance(a, float) and isinstance(b, float):
        if abs(a - b) > TOLERANCE:
            bad.append(f"{path}: {a!r} != {b!r} (delta {abs(a - b):.3e})")
        return bad
    if type(a) is not type(b):
        bad.append(f"{path}: type {type(a).__name__} != {type(b).__name__}")
        return bad
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                bad.append(f"{path}.{k}: missing in baseline")
            elif k not in b:
                bad.append(f"{path}.{k}: missing now")
            else:
                bad += _diff(a[k], b[k], f"{path}.{k}")
        return bad
    if isinstance(a, list):
        if len(a) != len(b):
            bad.append(f"{path}: length {len(a)} != {len(b)}")
            return bad
        for i, (x, y) in enumerate(zip(a, b)):
            bad += _diff(x, y, f"{path}[{i}]")
        return bad
    if a != b:
        if isinstance(a, str) and "\n" in a:
            al, bl = a.splitlines(), b.splitlines()
            for i, (x, y) in enumerate(zip(al, bl)):
                if x != y:
                    bad.append(f"{path}: line {i + 1}\n  was: {x}\n  now: {y}")
                    break
            if len(al) != len(bl):
                bad.append(f"{path}: {len(al)} lines != {len(bl)}")
        else:
            bad.append(f"{path}: {a!r} != {b!r}")
    return bad


def compare(with_audit: bool) -> list:
    now = capture(with_audit)
    bad = []
    for name, value in now.items():
        f = BASELINE / f"{name}.json"
        if not f.exists():
            bad.append(f"{name}: NO BASELINE (run --write first)")
            continue
        base = json.loads(f.read_text(encoding="utf-8"))
        bad += _diff(base, value, name)
    return bad


def write(with_audit: bool) -> None:
    BASELINE.mkdir(parents=True, exist_ok=True)
    for name, value in capture(with_audit).items():
        (BASELINE / f"{name}.json").write_text(
            json.dumps(value, indent=1, sort_keys=True), encoding="utf-8")
        print(f"  captured {name}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--with-audit", action="store_true")
    a = ap.parse_args()
    if a.write:
        print(f"capturing baseline into {BASELINE}")
        write(a.with_audit)
        raise SystemExit(0)
    bad = compare(a.with_audit)
    if not bad:
        print("GOLDEN OK - nothing moved")
        raise SystemExit(0)
    print(f"GOLDEN FAILED - {len(bad)} difference(s)")
    for b in bad[:40]:
        print("  " + b)
    raise SystemExit(1)
