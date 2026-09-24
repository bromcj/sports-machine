"""The golden test: pin behaviour so a change that should move nothing can
prove it. Free; never writes to data_golden/.

    python tests/golden/capture.py --write     # capture the baseline
    python tests/golden/capture.py             # recapture and diff
    python -m pytest tests/golden/             # the same, as a test

WHAT IT PINS, and why each one:

  gates                   record(), record_paper() and arm() run on FIXED
                          inputs - including a result at 2 SE, which must fail
                          the 3 SE bar. This pins the gate CODE; editing a
                          sigma or a condition fails here.
  validation.json         the gates' own record, read from the repo. A RECORD,
  model/validation.py     not behaviour: it moves when the machine runs, and a
                          difference must be explained before re-capturing.
  feeds.odds_twin         every final Stats API game of the 2026 season -> its
                          odds twin or the reason there is none. Real matches,
                          refusals and doubleheaders are all in the set.
  predict_for_date        stored feature rows for two past dates -> the saved
                          model's margin and probability, to 1e-9. Pins model
                          loading and the margin mapping (home intercept
                          included). It does NOT rebuild features:
                          tests/test_live_park_factor.py and audit.py cover the
                          live feature path.
  market_score            season totals from score_finished(), plus a checksum
                          of market_close.
  paper-bet grading       every settled paper/placebo bet is un-settled on the
                          scratch copy and settled again by the real
                          bets.paper.settle() - closing_snapshot, fair_prob,
                          grade and _decompose_bet all run.
  monitor.run_checks      the operational view, with the clock frozen.

EVERYTHING RUNS ON A SCRATCH COPY of data_golden/, made fresh for each
capture. score_finished() and settle() write to the database; the first
version of this test ran them against data_golden itself, so one failing run
left the baseline data changed and the next run failed even after the code
was fixed.

THE CLOCK IS FROZEN at FROZEN_NOW for monitor and market scoring, which ask
"what is 12 hours ago" and "what is today". Unfrozen, the monitor pin started
failing on 2026-09-24 about seven hours after capture with no code change.

TIMESTAMPS in human-readable output are normalized to a token; structured
captures are not normalized, because there the values are what is pinned.

FLOATING POINT. TOLERANCE is one visible constant, never above 1e-9.

WHICH COPY. data_golden/ is restored from a verified backup and never
written to. It lives only in the development checkout; without it the test
skips, and this script refuses rather than create an empty one.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

BASELINE = Path(__file__).parent / "baseline"
GOLDEN = ROOT / "data_golden"

# The smallest tolerance that passes. Never widened silently, never above 1e-9.
TOLERANCE = 1e-9

# The instant data_golden was captured around (the 23:58 ET backup, re-baselined
# at 00:50 ET on 2026-09-24).
FROZEN_NOW = "2026-09-24T04:50:00+00:00"

# Things that legitimately differ between two runs of identical code.
NORMALIZE = [
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?"
                r"(\+00:00|Z)?"), "<TS>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}"), "<DATE>"),
    (re.compile(r"\b\d+\.\d+s\b"), "<DUR>"),
    (re.compile(r"\b\d+ day\(s\) behind"), "<LAG> day(s) behind"),
    (re.compile(r"\b\d+ day\(s\) old"), "<AGE> day(s) old"),
    (re.compile(r"\b\d{2,3},?\d{3} left of"), "<CREDITS> left of"),
    (re.compile(r"[A-Za-z]:\\[^\s\"']+"), "<PATH>"),
]

# Prepended to subprocess code that must not see the real clock. The modules
# do `import datetime as dt` and call dt.datetime.now() / dt.date.today(), so
# swapping their `dt` for a shim freezes exactly those calls.
FREEZE = r"""
import datetime as _real
class _DT(_real.datetime):
    @classmethod
    def now(cls, tz=None):
        t = _real.datetime.fromisoformat(%r)
        return t if tz is not None else t.replace(tzinfo=None)
class _D(_real.date):
    @classmethod
    def today(cls):
        return _real.datetime.fromisoformat(%r).date()
class _Shim:
    datetime, date = _DT, _D
    timedelta, timezone = _real.timedelta, _real.timezone
def freeze(*mods):
    for m in mods:
        m.dt = _Shim
""" % (FROZEN_NOW, FROZEN_NOW)

_DATA = None                      # the scratch copy for the current capture


def normalize(text: str) -> str:
    for pat, rep in NORMALIZE:
        text = pat.sub(rep, text)
    return text


def _env():
    e = dict(os.environ)
    e["SPORTS_MACHINE_DATA_DIR"] = str(_DATA or GOLDEN)
    e["PYTHONIOENCODING"] = "utf-8"
    e.pop("ODDS_API_KEY", None)
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
    """A structured capture. NOT normalized: the values are what is pinned.

    The payload is the LAST line of output, so anything a function prints on
    the way cannot be mistaken for it.
    """
    lines = [l for l in _raw(args).strip().splitlines() if l.strip()]
    try:
        return json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError):
        return fallback("\n".join(lines))


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


def cap_gates() -> list:
    """The gate code on fixed inputs, against a temp validation.json."""
    code = r"""
import json, tempfile, pathlib
import model.validation as v
v.PATH = pathlib.Path(tempfile.mkdtemp()) / "validation.json"
out = []
def rec(name, r):
    out.append([name, r.get("cleared", r.get("passed")), r["reason"]])
seasons = [
    {"season": 2024, "logloss_model": 0.670, "logloss_market": 0.674, "n_games": 2400, "ll_diff_sd": 0.03},
    {"season": 2025, "logloss_model": 0.672, "logloss_market": 0.675, "n_games": 2400, "ll_diff_sd": 0.03},
    {"season": 2026, "logloss_model": 0.675, "logloss_market": 0.678, "n_games": 2300, "ll_diff_sd": 0.03}]
rec("gate1 placeholder, big margin", v.record("a", v.PLACEHOLDER, seasons))
rec("gate1 real market, beats every season", v.record("b", v.REAL_MARKET, seasons))
lost = [dict(s) for s in seasons]; lost[1]["logloss_model"] = 0.676
rec("gate1 real market, loses one season", v.record("c", v.REAL_MARKET, lost))
noisy = [dict(s, ll_diff_sd=2.0) for s in seasons]
rec("gate1 real market, inside the noise", v.record("d", v.REAL_MARKET, noisy))
slots = [v.SLOTS[i % 3] for i in range(60)]
def series(mean, sd, n=60):
    return [mean + sd * (1 if i % 2 else -1) for i in range(n)]
se = 1.0 / 60 ** 0.5                                    # sd 1 -> SE 0.129
rec("gate2 at 2 SE", v.record_paper("e", series(2 * se, 1.0), slots, 0.9))
rec("gate2 at 3.5 SE", v.record_paper("f", series(3.5 * se, 1.0), slots, 0.9))
rec("gate2 at 3.5 SE, 49 bets", v.record_paper("g", series(3.5 * se, 1.0, 49), slots[:49], 0.9))
rec("gate2 at 3.5 SE, coverage 0.74", v.record_paper("h", series(3.5 * se, 1.0), slots, 0.74))
rec("gate2 at 3.5 SE, placebo passes too", v.record_paper("i", series(3.5 * se, 1.0), slots, 0.9, placebo=series(3.5 * se, 1.0)))
rec("gate2 at 3.5 SE, all late games", v.record_paper("j", series(3.5 * se, 1.0), ["late"] * 60, 0.8))
out.append(["arm, gate 1 failed", v.arm("c")])
v.record("f", v.REAL_MARKET, seasons)
out.append(["arm, both passed", v.arm("f")])
out.append(["cleared after arm", v.is_cleared("f")])
out.append(["cleared, placeholder", v.is_cleared("a")])
print(json.dumps(out))
"""
    return _json(["-c", code], lambda t: [["PARSE_FAILED", t[:400]]])


def cap_monitor() -> list:
    code = FREEZE + r"""
import json, monitor
freeze(monitor)
print(json.dumps([{k: v for k, v in f.items() if k != 'detail'}
                  | {'detail': f.get('detail', '')} for f in monitor.run_checks()],
                 sort_keys=True, default=str))
"""
    return _json(["-c", code], lambda t: [{"PARSE_FAILED": t[:400]}])


def cap_twins() -> list:
    """Every final Stats API game of 2026 -> its odds twin, or why not."""
    code = r"""
import json
from db import connect
from feeds import odds_twin, SQL_STATS_API
con = connect()
rows = con.execute(
    "SELECT game_id FROM games WHERE sport='mlb' AND (%s) AND status='final'"
    " AND game_date >= '2026-01-01' ORDER BY game_id" % SQL_STATS_API).fetchall()
out = []
for r in rows:
    twin, note = odds_twin(con, r["game_id"])
    out.append([r["game_id"], twin or "", note or ""])
print(json.dumps(out))
"""
    return _json(["-c", code], lambda t: [["PARSE_FAILED", t[:400], ""]])


def cap_predictions() -> dict:
    """Two fixed past dates: stored features + saved model -> probability.

    Re-derived rather than replayed. `predict_for_date` returns a COUNT and
    WRITES to the append-only predictions table; this takes the same path it
    takes internally (latest feature row, predict_margin, win_prob) without
    the write.
    """
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
    code = FREEZE + r"""
import json, hashlib
from db import connect
import model.market_score as ms
freeze(ms)
con = connect()
rows = con.execute("SELECT game_id, odds_game_id, ROUND(p_fair_home, 10),"
                   " source FROM market_close ORDER BY game_id").fetchall()
h = hashlib.sha256()
for r in rows:
    h.update(("|".join(str(x) for x in tuple(r))).encode())
out = {"market_close_rows": len(rows), "market_close_sha": h.hexdigest()}
try:
    s = ms.score_finished("mlb", limit_days=100000)
    out["score_finished"] = {k: (round(v, 10) if isinstance(v, float) else v)
                             for k, v in s.items()} if isinstance(s, dict) else str(s)
except Exception as e:
    out["score_finished"] = ["ERROR", type(e).__name__, str(e)[:120]]
print(json.dumps(out, sort_keys=True))
"""
    return _json(["-c", code], lambda t: {"PARSE_FAILED": t[:400]})


def cap_grading() -> list:
    """Un-settle every paper/placebo bet on the scratch copy, settle again with
    the real code, and pin what comes out."""
    code = r"""
import contextlib, io, json
from db import connect
from bets import paper
con = connect()
con.execute("UPDATE bets SET result=NULL, pnl=NULL, closing_line=NULL,"
            " clv_pct=NULL, ev_fair_close=NULL, shop_pct=NULL, info_pct=NULL,"
            " fair_source=NULL WHERE mode IN ('paper','placebo')")
con.commit()
con.close()
with contextlib.redirect_stdout(io.StringIO()):
    tally = paper.settle("mlb")
con = connect()
rows = con.execute(
    "SELECT bet_id, mode, side, line_taken, closing_line, clv_pct,"
    " ev_fair_close, shop_pct, info_pct, fair_source, result, pnl"
    " FROM bets ORDER BY bet_id").fetchall()
def r10(x):
    return round(x, 10) if isinstance(x, float) else x
print(json.dumps([sorted(tally.items())] + [[r10(v) for v in tuple(r)] for r in rows]))
"""
    return _json(["-c", code], lambda t: [["PARSE_FAILED", t[:400]]])


FAST = {
    "gates": cap_gates,
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
    global _DATA
    if not (GOLDEN / "machine.db").exists():
        raise SystemExit(f"No golden data at {GOLDEN} - see the module "
                         "docstring. Refusing rather than create an empty one.")
    tmp = Path(tempfile.mkdtemp(prefix="golden-"))
    try:
        _DATA = tmp / "data"
        shutil.copytree(GOLDEN, _DATA)
        out = {name: fn() for name, fn in FAST.items()}
        if with_audit:
            out.update({name: fn() for name, fn in SLOW.items()})
        return out
    finally:
        _DATA = None
        shutil.rmtree(tmp, ignore_errors=True)


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
            else:
                if len(al) != len(bl):
                    bad.append(f"{path}: {len(al)} lines != {len(bl)}")
                else:
                    # Same lines, different string: line endings or a
                    # trailing newline. Still a difference.
                    bad.append(f"{path}: whitespace or line endings differ")
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
