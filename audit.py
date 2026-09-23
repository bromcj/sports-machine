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
    # ------------------------------------------------------------ CLV window
    section("CLV GRADING WINDOW")
    import db as dbmod3
    from bets import log as blog
    real3 = dbmod3.DB_PATH
    try:
        tmp3 = pathlib.Path(tempfile.mkdtemp())
        dbmod3.DB_PATH = tmp3 / "clv.db"
        dbmod3.init()
        blog.connect = dbmod3.connect
        c3 = dbmod3.connect()
        for i in range(3):
            c3.execute(
                "INSERT INTO bets (ts, game_id, sport, side, book, line_taken,"
                " stake, model_prob, novig_market_prob, edge, kelly_fraction)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (dbmod3.utc_now(), f"g{i}", "mlb", "home", "fanduel",
                 -110, 10.0, 0.55, 0.52, 0.03, 0.01))
        c3.commit()
        c3.close()

        # Inside the window: CLV is recorded.
        blog.grade(1, -130, True, minutes_before_start=20)
        # Outside it: settled, but no CLV. This is the case that matters -
        # the archive's median nearest price is ~5.7 HOURS before first pitch.
        blog.grade(2, -130, True, minutes_before_start=342)
        # Not supplied at all: unknown means ungraded, never "assume it counts".
        blog.grade(3, -130, True)

        c3 = dbmod3.connect()
        got = {r["bet_id"]: (r["clv_pct"], r["result"])
               for r in c3.execute("SELECT bet_id, clv_pct, result FROM bets")}
        c3.close()
        check("CLV is graded when the close is inside the window",
              got[1][0] is not None, f"clv={got[1][0]}")
        check("CLV is NOT graded when the close is hours early",
              got[2][0] is None, f"clv={got[2][0]}, {blog.CLOSING_WINDOW_MIN} min window")
        check("CLV is NOT graded when no closing time is supplied",
              got[3][0] is None, f"clv={got[3][0]}")
        check("an ungraded close still settles the bet for P&L",
              got[2][1] == "W" and got[3][1] == "W",
              "result recorded either way")
    finally:
        dbmod3.DB_PATH = real3
        blog.connect = dbmod3.connect

    # -------------------------------------------------------- ingest quality
    section("INGEST QUALITY")
    from ingest import quality as q
    # Each of these is something that cannot be true, so a validator that lets
    # it through is not being cautious, it is being useless.
    impossible = [
        ("moneyline 0", q.moneyline(0)),
        ("moneyline -50 (between -100 and +100)", q.moneyline(-50)),
        ("moneyline +99", q.moneyline(99)),
        ("moneyline 500000", q.moneyline(500_000)),
        ("non-numeric moneyline", q.moneyline("abc")),
        ("a team playing itself", q.teams("NYY", "NYY")),
        ("an empty team name", q.teams("", "BOS")),
        ("a negative score", q.score(-1)),
        ("a row with no price on either side", q.snapshot(None, None)),
        ("a game with no date", q.game("NYY", "BOS", None)),
    ]
    missed = [name for name, reason in impossible if reason is None]
    check(f"ingest rejects all {len(impossible)} impossible values", not missed,
          "all caught" if not missed else "let through: " + ", ".join(missed))

    legitimate = [
        ("-110 / -110", q.snapshot(-110, -110)),
        ("a heavy favourite (-750 / +460)", q.snapshot(-750, 460)),
        ("exactly -100 / +100", q.snapshot(-100, 100)),
        ("one side unpriced", q.snapshot(-110, None)),
        ("a 0-0 final", q.game("NYY", "BOS", "2026-09-22", 0, 0)),
        ("a scheduled game with no score", q.game("NYY", "BOS", "2026-09-22")),
    ]
    wrong = [f"{name} ({why})" for name, why in legitimate if why is not None]
    check("ingest keeps every legitimate value", not wrong,
          "all kept" if not wrong else "rejected: " + "; ".join(wrong))

    # The rules must also agree with everything already collected. A validator
    # that would have discarded real history is too aggressive, and this is the
    # only way to find that out without waiting for a quiet night.
    dbp = ROOT / "data" / "machine.db"
    if dbp.exists():
        c2 = sqlite3.connect(dbp)
        c2.row_factory = sqlite3.Row
        tbls = {r[0] for r in c2.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if {"odds_snapshots", "games"} <= tbls:
            bad_s = sum(1 for r in c2.execute(
                "SELECT away_ml, home_ml FROM odds_snapshots")
                if q.snapshot(r["away_ml"], r["home_ml"]))
            bad_g = sum(1 for r in c2.execute(
                "SELECT away, home, game_date, away_score, home_score FROM games")
                if q.game(r["away"], r["home"], r["game_date"],
                          r["away_score"], r["home_score"]))
            tot = c2.execute("SELECT (SELECT COUNT(*) FROM odds_snapshots)"
                             " + (SELECT COUNT(*) FROM games)").fetchone()[0]
            check("the rules reject nothing already collected",
                  bad_s == 0 and bad_g == 0,
                  f"{tot:,} stored rows, {bad_s + bad_g} would be rejected")
        else:
            skip("the rules reject nothing already collected", "no tables yet")
        c2.close()
    else:
        skip("the rules reject nothing already collected", "no database yet")

    # ----------------------------------------------------------- durability
    section("DURABILITY")
    # `backup` is rebound below as validation.json's saved text, so alias it.
    import backup as bkmod
    tmpdir = pathlib.Path(tempfile.mkdtemp())
    db_file = ROOT / "data" / "machine.db"
    if db_file.exists():
        torn = tmpdir / "torn.db"
        raw = db_file.read_bytes()
        torn.write_bytes(raw[:len(raw) // 3])     # what a mid-write copy leaves
        ok_torn, why_torn = bkmod.verify(torn)
        check("backup verification rejects a torn file", not ok_torn, why_torn)

        hollow = tmpdir / "hollow.db"
        c = sqlite3.connect(hollow)
        c.execute("CREATE TABLE games (x INT)")
        c.execute("CREATE TABLE odds_snapshots (x INT)")
        c.commit()
        c.close()
        ok_hollow, why_hollow = bkmod.verify(hollow)
        # A valid-but-empty SQLite file is the failure mode a naive check
        # misses: it opens fine and integrity_check passes.
        check("backup verification rejects an empty database",
              not ok_hollow, why_hollow)
    else:
        skip("backup verification rejects a torn file", "no data/machine.db")
        skip("backup verification rejects an empty database", "no data/machine.db")

    backups = sorted(bkmod.backup_dir().glob("machine-*.db"))
    if backups:
        newest = backups[-1]
        age_days = (dt.datetime.now()
                    - dt.datetime.fromtimestamp(newest.stat().st_mtime)).days
        ok_b, detail_b = bkmod.verify(newest)
        check("the newest backup actually opens", ok_b, detail_b)
        check("a backup exists from the last 7 days", age_days <= 7,
              f"{newest.name} is {age_days} day(s) old")
    else:
        skip("the newest backup actually opens", "no backups yet")
        skip("a backup exists from the last 7 days",
             f"none in {bkmod.backup_dir()} — run `python backup.py`")

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
        # A decisive win: 0.04 per game against a 0.30 spread over 2000 games
        # is ~6 SE. Comfortably real.
        rows = [{"season": 2025, "logloss_model": .61, "logloss_market": .65,
                 "n_games": 2000, "ll_diff_sd": 0.30}]
        v.record(probe, v.REAL_MARKET, rows)
        check("beating the market alone does not clear", not v.is_cleared(probe))
        # 60 bets averaging +1.4% with modest spread: comfortably real.
        strong = [1.4 + (i % 7 - 3) * 0.4 for i in range(60)]
        v.record_paper(probe, strong)
        check("adding positive CLV alone does not clear", not v.is_cleared(probe))
        v.arm(probe)
        check("all three gates open the tap", v.is_cleared(probe))
        v.record(probe, v.REAL_MARKET, rows)
        check("a fresh walk-forward auto-disarms", not v.is_cleared(probe))

        # Gate 2 must distinguish a real edge from a coin flip. Measured on
        # this archive, per-bet CLV has SD ~2.97%, so over 50 bets a zero-skill
        # model lands above zero about half the time. "Average is positive"
        # was therefore not a test of anything.
        noisy = [(2.97 if i % 2 else -2.85) for i in range(60)]   # mean +0.06%
        r = v.record_paper(probe, noisy)
        check("gate 2 rejects a positive average that is inside the noise",
              not r["passed"], r["reason"])
        r = v.record_paper(probe, strong)
        check("gate 2 accepts an average clear of the noise",
              r["passed"], r["reason"])
        r = v.record_paper(probe, strong[:40])
        check("gate 2 still enforces the 50-bet floor independently",
              not r["passed"], r["reason"])

        # Gate 1 must do the same job. MLB beats its baseline in all three
        # test seasons, but two of those margins are ~0.6 SE - a rule that
        # only compares season averages cannot tell that from skill.
        thin = [{"season": 2024, "logloss_model": .6900, "logloss_market": .6910,
                 "n_games": 2179, "ll_diff_sd": 0.14},
                {"season": 2025, "logloss_model": .6905, "logloss_market": .6912,
                 "n_games": 2187, "ll_diff_sd": 0.14},
                {"season": 2026, "logloss_model": .6902, "logloss_market": .6909,
                 "n_games": 2131, "ll_diff_sd": 0.14}]
        e = v.record(probe, v.REAL_MARKET, thin)
        check("gate 1 rejects winning every season by less than the noise",
              not e["cleared"], e["reason"])

        fat = [dict(r, logloss_model=.6850) for r in thin]
        e = v.record(probe, v.REAL_MARKET, fat)
        check("gate 1 accepts a pooled margin clear of the noise",
              e["cleared"], e["reason"])

        # Season averages with no spread cannot be judged, so they must not
        # clear. Failing open here would be the whole point of the gate lost.
        bare = [{"season": r["season"], "logloss_model": .6850,
                 "logloss_market": r["logloss_market"]} for r in thin]
        e = v.record(probe, v.REAL_MARKET, bare)
        check("gate 1 refuses to clear without per-game spread",
              not e["cleared"], e["reason"])
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
