"""Self-audit: prove the machine is still sound, from live data.

    python audit.py

Every check re-derives its answer rather than trusting a comment or a previous
run. It is safe to run any time: it writes only to temporary directories,
restores validation.json afterwards, and never touches archive/ or the odds API.

Exit code is 0 when everything passes and 1 otherwise, so it can gate a cron
job or a release. Checks whose inputs are absent (a fresh clone with no
data/ yet) report SKIP rather than failing - a missing backfill is not a bug.

Written after an end-to-end audit that found four real defects; each check here
corresponds to one of them, or to an invariant whose quiet failure would be
expensive:

  - the three data feeds mint incompatible game ids, so odds and results could
    not be joined and CLV was impossible to compute
  - the healthcheck failed the run on empty slates, emailing the owner daily
    for weeks during the dead windows inside an "in-season" month
  - NFL ties were scored as away wins
  - rolling features must exclude the game they describe, or the model learns
    from the answer
"""
import datetime as dt
import pathlib
import sqlite3
import sys
import tempfile
import warnings

ROOT = pathlib.Path(__file__).parent
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

RESULTS = []


def record(name, status, detail=""):
    RESULTS.append((status, name))
    print(f"  [{status:4s}] {name}" + (f"  - {detail}" if detail else ""))


def check(name, ok, detail=""):
    record(name, "PASS" if ok else "FAIL", detail)


def skip(name, why):
    record(name, "SKIP", why)


def section(title):
    print(f"\n{title}")


def main() -> int:
    print("=" * 78)
    print(f"SELF-AUDIT  {ROOT}")
    print("=" * 78)

    # ---------------------------------------------------------------- basics
    section("LOCATION & INTEGRITY")
    from db import DB_PATH
    check("database is outside a cloud-sync folder",
          not any(p in str(DB_PATH) for p in ("OneDrive", "Dropbox", "Google Drive")),
          str(DB_PATH))
    if DB_PATH.exists():
        q = sqlite3.connect(DB_PATH).execute("PRAGMA quick_check").fetchone()[0]
        check("database integrity", q == "ok", q)
    else:
        skip("database integrity", "no database yet - run: python db.py")

    mods = ("db healthcheck backfill run_daily bets.log bets.engine merge_archive "
            "export_snapshots ingest.odds ingest.scores ingest.mlb model.train "
            "model.calibrate model.validation model.persist model.predict "
            "features.build features.build_training").split()
    try:
        for m in mods:
            __import__(m)
        check(f"all {len(mods)} modules import", True)
    except Exception as e:
        check("all modules import", False, repr(e))
        return 1

    # ------------------------------------------------------------- identity
    section("RECORD IDENTITY ACROSS FEEDS")
    from bets.log import odds_twin
    from db import connect
    con = connect()
    # connect() creates the file but not the tables; init() does that. On a
    # fresh clone there is nothing to inspect yet.
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"games", "odds_snapshots", "predictions"} <= tables:
        skip("feeds share no game_id, so resolution is still required",
             "no tables yet - run: python db.py")
        skip("odds_twin bridges predictions to prices", "no tables yet")
        con.close()
        con = None
    if con is not None:
        both = con.execute(
            "SELECT COUNT(*) c FROM games g WHERE g.status='final'"
            " AND g.home_score IS NOT NULL"
            " AND g.game_id IN (SELECT game_id FROM odds_snapshots)").fetchone()["c"]
        check("feeds share no game_id, so resolution is still required", both == 0,
              f"{both} games carry both odds and a score")
        preds = con.execute("SELECT game_id FROM predictions LIMIT 20").fetchall()
        if preds:
            notes = [odds_twin(con, p["game_id"]) for p in preds]
            got = sum(1 for t, _ in notes if t)
            amb = sum(1 for _, n in notes if n and "ambiguous" in n)
            check("odds_twin bridges predictions to prices", got > 0,
                  f"{got}/{len(preds)} resolved, {amb} refused as ambiguous")
        else:
            skip("odds_twin bridges predictions to prices", "no predictions stored yet")
        con.close()

    # ------------------------------------------------------------ staleness
    section("STALENESS & REFRESH")
    import pandas as pd
    sc = sorted((ROOT / "data" / "statcast").glob("*.parquet"))
    if sc:
        latest = max(pd.read_parquet(p)["game_date"].max() for p in sc)
        gap = (pd.Timestamp(dt.date.today()) - pd.Timestamp(latest)).days
        check("statcast lag is visible", True,
              f"{gap} day(s) behind (Statcast itself lags ~1)")
    else:
        skip("statcast lag is visible", "no statcast parquets - run: python backfill.py")

    if (ROOT / "archive").exists():
        import db as dbmod
        import merge_archive
        real = dbmod.DB_PATH
        try:
            dbmod.DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "m.db"
            dbmod.init()
            merge_archive.connect = dbmod.connect
            merge_archive.merge()
            a = dbmod.connect().execute("SELECT COUNT(*) c FROM odds_snapshots").fetchone()["c"]
            merge_archive.merge()
            b = dbmod.connect().execute("SELECT COUNT(*) c FROM odds_snapshots").fetchone()["c"]
            check("merge_archive is idempotent", a == b, f"{a} rows, unchanged on re-merge")
        finally:
            dbmod.DB_PATH = real
    else:
        skip("merge_archive is idempotent", "no archive/ yet")

    # ------------------------------------------------------------- calendar
    section("CALENDAR / FALSE ALARMS")
    import db as dbmod
    import healthcheck
    real, real_status, real_root = dbmod.DB_PATH, healthcheck.STATUS, healthcheck.ROOT
    try:
        tmp = pathlib.Path(tempfile.mkdtemp())
        dbmod.DB_PATH = tmp / "t.db"
        dbmod.init()
        healthcheck.connect = dbmod.connect
        healthcheck.STATUS = tmp / "STATUS.md"
        healthcheck.ROOT = ROOT
        quiet = healthcheck.check()
        con = dbmod.connect()
        today = dt.date.today().isoformat()
        for i in range(3):
            con.execute("INSERT INTO games (game_id, sport, game_date, away, home)"
                        " VALUES (?,?,?,?,?)", (f"mlb-{i}", "mlb", today, "A", "B"))
        con.commit()
        con.close()
        loud = healthcheck.check()
    finally:
        dbmod.DB_PATH, healthcheck.STATUS, healthcheck.ROOT = real, real_status, real_root
    check("an empty slate does NOT alert the owner", quiet == 0, f"exit {quiet}")
    check("a priced-out slate still does", loud == 1, f"exit {loud}")

    # -------------------------------------------------------------- model
    section("MODEL INTEGRITY")
    from bets.engine import novig_probs
    a_, h_ = novig_probs(-110, -110)
    check("de-vig exact on -110/-110", abs(a_ - .5) < 1e-9 and abs(h_ - .5) < 1e-9)
    a2, h2 = novig_probs(+150, -170)
    check("de-vig sums to 1 on +150/-170", abs(a2 + h2 - 1) < 1e-9)

    mlb_p = ROOT / "data" / "training_mlb.parquet"
    nfl_p = ROOT / "data" / "training_nfl.parquet"
    if mlb_p.exists():
        mlb = pd.read_parquet(mlb_p)
        check("no MLB ties (extra innings decide)", (mlb["run_diff"] == 0).sum() == 0)
        if sc:
            from features.sports.mlb_features import load_statcast
            scd = load_statcast()
            row = mlb[mlb.season == mlb.season.max()].iloc[len(mlb) // 20]
            pa = scd[(scd.woba_denom > 0) & (scd.bat_team == row.home_ab)]
            w = lambda d_: d_.woba_value.sum() / d_.woba_denom.sum()
            span = pd.Timedelta(days=30)
            excl = pa[(pa.game_date < row.game_date) & (pa.game_date >= row.game_date - span)]
            incl = pa[(pa.game_date <= row.game_date) & (pa.game_date > row.game_date - span)]
            check("rolling features exclude the game they describe",
                  abs(w(excl) - row.home_off_woba_30d) < 1e-6
                  and abs(w(incl) - row.home_off_woba_30d) > 1e-9,
                  "recomputed by hand from raw Statcast")
        else:
            skip("rolling features exclude the game they describe", "no statcast")
    else:
        skip("no MLB ties (extra innings decide)", "no training table yet")

    if nfl_p.exists():
        nfl = pd.read_parquet(nfl_p)
        check("NFL ties dropped, not scored as away wins",
              (nfl["point_diff"] == 0).sum() == 0)
        d = nfl.dropna(subset=["market_spread"])
        hp = d[d.market_spread > 0]["home_won"].mean()
        ap = d[d.market_spread < 0]["home_won"].mean()
        check("spread_line sign convention holds", hp > ap,
              f"home favored wins {hp:.1%} vs {ap:.1%}")
    else:
        skip("NFL ties dropped, not scored as away wins", "no NFL training table")

    # --------------------------------------------------------------- guard
    section("BETTING GUARD")
    from model import validation as v
    live = ROOT / "validation.json"
    backup = live.read_text(encoding="utf-8") if live.exists() else None
    try:
        for sport in ("mlb", "nfl"):
            if v.status(sport):
                check(f"{sport} is not cleared to bet", not v.is_cleared(sport),
                      v.status(sport).get("reason", ""))
        probe = "_audit_probe"
        rows = [{"season": 2025, "logloss_model": .61, "logloss_market": .65}]
        v.record(probe, v.REAL_MARKET, rows)
        check("beating the market alone does not clear", not v.is_cleared(probe))
        v.record_paper(probe, 60, 1.4)
        check("adding positive CLV alone does not clear", not v.is_cleared(probe))
        v.arm(probe)
        check("all three gates open the tap", v.is_cleared(probe))
        v.record(probe, v.REAL_MARKET, rows)
        check("a fresh walk-forward auto-disarms", not v.is_cleared(probe))
    finally:
        # Leave validation.json exactly as found. If it did not exist, the probe
        # created it, so remove it rather than leaving a fake sport behind.
        if backup is not None:
            live.write_text(backup, encoding="utf-8")
        elif live.exists():
            live.unlink()

    return _finish()


def _finish() -> int:
    n_pass = sum(1 for s, _ in RESULTS if s == "PASS")
    n_fail = sum(1 for s, _ in RESULTS if s == "FAIL")
    n_skip = sum(1 for s, _ in RESULTS if s == "SKIP")
    print("\n" + "=" * 78)
    print(f"RESULT: {n_pass} passed, {n_fail} failed, {n_skip} skipped")
    for status, name in RESULTS:
        if status == "FAIL":
            print(f"  FAILED: {name}")
    print("=" * 78)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
