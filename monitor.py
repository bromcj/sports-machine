"""Health checks for the unattended local job, appended to logs/health.jsonl.

    python monitor.py            run the checks, print and append
    python monitor.py --tail 20  the last 20 findings

Different job from healthcheck.py, which asks "did THIS run collect what it
should" and fails the cloud run so GitHub emails you. This asks "is the system
as a whole still in a state that makes sense", from the local database, where
all the data actually is.

Every finding is one JSON object per line, so history is greppable and a
future dashboard can read it without parsing prose.

Levels:
  CRITICAL  data is being lost or corrupted right now
  ERROR     something is broken and will not fix itself
  WARNING   worth looking at; may resolve on its own
  INFO      normal state, recorded so its absence is noticeable

Findings are delivered by notify.py - see there for the channels.
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
import paths
from db import connect
from feeds import SQL_STATS_API, parse_utc

LOG = ROOT / "logs" / "health.jsonl"

# Delivery lives in notify.py: ALERTS.md always, a desktop notification for
# anything serious, and ntfy.sh only if SPORTS_MACHINE_NTFY_TOPIC is set.
# Nothing leaves this machine by default.
ALERT_AT = ("CRITICAL", "ERROR")

# A started game should be final long before this.
STALE_GAME_HOURS = 12
# A paper bet whose game finished should settle on the next nightly run.
UNSETTLED_BET_HOURS = 36
# Feeds disagree by minutes on a delayed game, never by hours.
FEED_GAP_MIN = 180
MIN_PITCHES = 200
MAX_SKIPPED_SHARE = 0.20
# A ridge model on run differential should sit near the true home rate. Far
# outside this and something has drifted - a feature scale, a sign, the k.
HOME_PROB_RANGE = (0.50, 0.56)
CREDITS_WARN, CREDITS_CRIT = 0.25, 0.10


def _find(level, check, detail, **extra):
    return {"level": level, "check": check, "detail": detail, **extra}


def run_checks() -> list[dict]:
    con = connect()
    now = dt.datetime.now(dt.timezone.utc)
    out = []
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}

    # --- a started game that never finished ------------------------------
    if "games" in tables:
        stale = con.execute(
            f"SELECT COUNT(*) FROM games WHERE sport='mlb' AND ({SQL_STATS_API})"
            f" AND status IN ('scheduled','live') AND start_time_utc IS NOT NULL"
            f" AND start_time_utc < ?",
            ((now - dt.timedelta(hours=STALE_GAME_HOURS)).isoformat(),)
        ).fetchone()[0]
        out.append(_find("ERROR" if stale else "INFO",
                         "games reach a final state",
                         f"{stale} game(s) started over {STALE_GAME_HOURS}h ago "
                         f"and are still not final", count=stale))

        # --- a final that cannot have happened ---------------------------
        bad = con.execute(
            "SELECT COUNT(*) FROM games WHERE status='final' AND ("
            " away_score IS NULL OR home_score IS NULL OR"
            " (sport='mlb' AND away_score=0 AND home_score=0))").fetchone()[0]
        out.append(_find("CRITICAL" if bad else "INFO",
                         "no impossible finals",
                         f"{bad} final(s) with a missing or 0-0 score", count=bad))

        # --- a feed match that is a different game -----------------------
        # Uses odds_twin, which picks the CLOSEST counterpart. The first
        # version of this joined on (away, home) and compared every pair,
        # which flagged a Tampa Bay DOUBLEHEADER - two real games six hours
        # apart - as ten feed disagreements.
        from feeds import odds_twin
        gap = 0
        for r in con.execute(
                f"SELECT game_id, start_time_utc FROM games WHERE sport='mlb'"
                f" AND ({SQL_STATS_API}) AND start_time_utc IS NOT NULL"
                f" AND game_date >= date('now','-7 day')"):
            twin, _ = odds_twin(con, r["game_id"])
            if twin is None:
                continue
            o = con.execute("SELECT start_time_utc FROM games WHERE game_id=?",
                            (twin,)).fetchone()
            x, y = parse_utc(r["start_time_utc"]), parse_utc(o["start_time_utc"])
            if x and y and abs((x - y).total_seconds()) / 60 > FEED_GAP_MIN:
                gap += 1
        out.append(_find("ERROR" if gap else "INFO",
                         "feed matches agree on first pitch",
                         f"{gap} match(es) more than {FEED_GAP_MIN} min apart",
                         count=gap))

    # --- paper bets that never settled -----------------------------------
    if "bets" in tables:
        unsettled = con.execute(
            f"SELECT COUNT(*) FROM bets b JOIN games g ON g.game_id=b.game_id"
            f" WHERE b.mode IN ('paper','placebo') AND b.result IS NULL"
            f"   AND g.start_time_utc < ?",
            ((now - dt.timedelta(hours=UNSETTLED_BET_HOURS)).isoformat(),)
        ).fetchone()[0]
        out.append(_find("ERROR" if unsettled else "INFO",
                         "paper bets settle",
                         f"{unsettled} bet(s) unsettled over "
                         f"{UNSETTLED_BET_HOURS}h after first pitch",
                         count=unsettled))

        # --- a bet priced after first pitch ------------------------------
        late = con.execute(
            "SELECT COUNT(*) FROM bets b"
            " JOIN odds_snapshots o ON o.id = b.odds_snapshot_id"
            " JOIN games g ON g.game_id = b.game_id"
            " WHERE b.odds_snapshot_id IS NOT NULL"
            "   AND g.start_time_utc IS NOT NULL AND o.ts >= g.start_time_utc"
        ).fetchone()[0]
        out.append(_find("CRITICAL" if late else "INFO",
                         "no bet used a price from after first pitch",
                         f"{late} bet(s) priced at or after first pitch",
                         count=late))

    # --- statcast freshness and completeness ------------------------------
    sc = sorted(paths.STATCAST_DIR.glob("*.parquet"))
    if sc:
        import pandas as pd
        d = pd.read_parquet(sc[-1], columns=["game_pk", "game_date"])
        newest = pd.to_datetime(d["game_date"]).max().date()
        lag = (dt.date.today() - newest).days
        out.append(_find("WARNING" if lag > 2 else "INFO",
                         "statcast is current",
                         f"{lag} day(s) behind (Savant itself lags ~1)", lag=lag))
        thin = int((d.groupby("game_pk").size() < MIN_PITCHES).sum())
        out.append(_find("WARNING" if thin else "INFO",
                         "no truncated statcast game",
                         f"{thin} game(s) under {MIN_PITCHES} pitches", count=thin))

    # --- how much of today's slate got predicted --------------------------
    if {"games", "predictions"} <= tables:
        today = dt.date.today().isoformat()
        sched = con.execute(
            f"SELECT COUNT(*) FROM games WHERE sport='mlb' AND ({SQL_STATS_API})"
            f" AND game_date=?", (today,)).fetchone()[0]
        got = con.execute(
            f"SELECT COUNT(DISTINCT p.game_id) FROM predictions p"
            f" JOIN games g ON g.game_id=p.game_id WHERE g.game_date=?",
            (today,)).fetchone()[0]
        share = 1 - (got / sched) if sched else 0.0
        out.append(_find("WARNING" if share > MAX_SKIPPED_SHARE else "INFO",
                         "most of the slate is predicted",
                         f"{got}/{sched} predicted, {share:.0%} skipped",
                         skipped_share=round(share, 3)))

        # --- has the model drifted? --------------------------------------
        row = con.execute(
            "SELECT AVG(home_win_prob) m, COUNT(*) n FROM predictions"
            " WHERE created_at >= ?",
            ((now - dt.timedelta(days=7)).isoformat(),)).fetchone()
        if row["n"]:
            lo, hi = HOME_PROB_RANGE
            ok = lo <= row["m"] <= hi
            out.append(_find("INFO" if ok else "WARNING",
                             "mean predicted home probability is sane",
                             f"{row['m']:.3f} over {row['n']} predictions "
                             f"(expect {lo:.2f}-{hi:.2f})",
                             mean_home_prob=round(row["m"], 4)))

    # --- API credits ------------------------------------------------------
    if "api_usage" in tables:
        row = con.execute(
            "SELECT remaining, used FROM api_usage ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row and row["remaining"] is not None:
            total = (row["remaining"] or 0) + (row["used"] or 0)
            frac = (row["remaining"] / total) if total else 1.0
            lvl = ("CRITICAL" if frac < CREDITS_CRIT else
                   "WARNING" if frac < CREDITS_WARN else "INFO")
            out.append(_find(lvl, "odds API credits",
                             f"{row['remaining']} left of {total} ({frac:.0%})",
                             remaining=row["remaining"]))
    out += scanner_checks(con, now, tables)
    con.close()
    return out


def _credit_level(frac_left: float) -> str:
    return ("CRITICAL" if frac_left < CREDITS_CRIT else
            "WARNING" if frac_left < CREDITS_WARN else "INFO")


def scanner_checks(con, now, tables) -> list[dict]:
    """The scanner's own limits and its polling loop. Silent until the
    scanner exists here (its tables, or polling switched on), so the
    scheduled job's findings do not change before then."""
    import config
    out = []
    if "credit_ledger" in tables:
        from scanner import budget
        s = budget.status(con, now)
        if s["brief_active"]:
            frac = s["brief_left"] / s["brief_cap"]
            out.append(_find(_credit_level(frac), "scanner credits: the brief's cap",
                             f"{s['brief_spent']:,} of {s['brief_cap']:,} spent"
                             f" ({frac:.0%} left)", spent=s["brief_spent"]))
        frac = s["month_left"] / s["month_budget"]
        out.append(_find(_credit_level(frac), "scanner credits: this month",
                         f"{s['month_spent']:,} of {s['month_budget']:,} spent"
                         f" ({frac:.0%} left)", spent=s["month_spent"]))
    if config.POLLING_ENABLED:
        from scanner import poll
        hb = poll.heartbeat()
        if poll.alive(hb, now):
            out.append(_find("INFO", "the polling loop is running",
                             f"{hb.get('state')} (level {hb.get('level')})"))
        elif hb and str(hb.get("state", "")).startswith("stopped"):
            out.append(_find("ERROR", "the polling loop is running",
                             f"it {hb['state']} - see logs/poll.log"))
        else:
            beat = parse_utc((hb or {}).get("beat_at"))
            ago = "never" if beat is None else \
                f"{(now - beat).total_seconds() / 60:.0f} min ago"
            out.append(_find("ERROR", "the polling loop is running",
                             f"no heartbeat since {ago}; the next scheduled run"
                             f" restarts it (`python run_daily.py poll --ensure`)"))
    return out


def run(quiet: bool = False) -> int:
    findings = run_checks()
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        for x in findings:
            f.write(json.dumps({"ts": stamp, **x}) + "\n")
    # Get it in front of the owner: ALERTS.md always, a desktop toast for
    # anything serious, and ntfy only if they have opted in.
    from notify import deliver
    sent = deliver(findings, stamp)

    bad = [x for x in findings if x["level"] in ALERT_AT]
    if not quiet:
        for x in findings:
            if x["level"] != "INFO" or not bad:
                print(f"  [{x['level']:8s}] {x['check']}: {x['detail']}")
        print(f"  {len(findings)} checks, {len(bad)} at {'/'.join(ALERT_AT)}")
        if bad:
            ch = ["ALERTS.md"]
            if sent["toast"]:
                ch.append("desktop notification")
            if sent["ntfy"]:
                ch.append("ntfy")
            print(f"  delivered via: {', '.join(ch)}")
    return 1 if bad else 0


def tail(n: int = 20):
    if not LOG.exists():
        print("No findings logged yet.")
        return
    lines = LOG.read_text(encoding="utf-8").splitlines()[-n:]
    for line in lines:
        x = json.loads(line)
        print(f"  {x['ts'][:16]}  [{x['level']:8s}] {x['check']}: {x['detail']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tail", type=int, nargs="?", const=20)
    a = ap.parse_args()
    if a.tail:
        tail(a.tail)
    else:
        raise SystemExit(run())
