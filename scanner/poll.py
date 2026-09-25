"""The polling loop: a long-running process that keeps the prices table fresh.

    python run_daily.py poll --plan     the credit arithmetic. Free, no calls.
    python run_daily.py poll --status   the heartbeat, the limits, what is due.
                                        Free, no calls.
    python run_daily.py poll --ensure   what the scheduled job runs: start the
                                        loop if it should be running and is
                                        not, restart it on new code. Free.
    python run_daily.py poll --live     run the loop in this window.
                                        COSTS CREDITS. Refuses unless
                                        config.POLLING_ENABLED is True and
                                        the data folder is the ledger of
                                        record (RECORD, below).

Every ~30 seconds it asks, per sport, whether a poll is due (scanner.budget:
the cadence by time to the soonest unstarted game, at the ladder level the
day's credits allow), and if so makes one sport-wide call and stores every
price through the sportsbook adapter. Start times come from the free events
list, refreshed hourly. Kalshi and Polymarket join as free sources in Phase C.

Safety, in the order it applies:
  - config.POLLING_ENABLED False: nothing starts, --live refuses, and
    --ensure asks a loop still running from before to stop (a running loop
    never rereads the flag). data/scanner/poll.stop stops it at once.
  - budget.check() before every metered call; OverBudget means no request.
    A daily-pace refusal waits for tomorrow; the brief's cap or the month's
    budget stops the loop.
  - no ledger row, no request: every call, free or not, is written to
    credit_ledger at its estimate BEFORE it is made (budget.reserve(), in
    the same transaction as the check), then filled in with what it cost,
    and written to api_usage so monitor.py's account check sees the balance.
  - a 401 (bad key) or 429 (out of credits / rate limit) stops the loop:
    retrying either spends nothing useful.

"Is it running" is judged by the heartbeat file's age and by an OS lock on
data/scanner/poll.lock that `--live` holds for its whole life - never by
probing a process id: on Windows, os.kill(pid, 0) does not test a process, it
ends it. The OS drops the lock when the process ends, however it ends, so a
second loop is refused even while the first one's heartbeat is old.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

import config
import db
import paths
from feeds import parse_utc
from ingest import http
from scanner import budget
from scanner.store import canon_ts

ROOT = Path(__file__).parent.parent
STATE_DIR = paths.DATA_DIR / "scanner"
HEARTBEAT = STATE_DIR / "poll.json"
STOP = STATE_DIR / "poll.stop"
LOCK = STATE_DIR / "poll.lock"
# The credit limits count the ledger in the data folder the loop runs
# against, but every checkout spends the same paid key: a loop in a second
# folder would start again from a fresh 6,000. So the loop runs only where
# this file says "this ledger is the one" - production's data folder. The
# owner creates it by hand there, in the step that turns polling on.
RECORD = STATE_DIR / "ledger-of-record"
LOG = ROOT / "logs" / "poll.log"

API = "https://api.the-odds-api.com/v4"
TICK_S = 30
SCHEDULE_EVERY = dt.timedelta(minutes=60)
LEVEL_EVERY = dt.timedelta(minutes=5)
ALIVE_WITHIN = dt.timedelta(minutes=5)       # a beat newer than this: running


class Fatal(RuntimeError):
    """Stop the loop: continuing cannot help. `kind` says whether it may come
    back on its own: 'budget' once the limits allow a call again (next day,
    next month); 'api' (a refused key, no credits left) only by hand."""

    def __init__(self, message, kind="api"):
        super().__init__(message)
        self.kind = kind


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ---------------------------------------------------------------- source ---

class OddsSource:
    """The Odds API. Metered for prices, free for the events list."""

    name = "odds"

    def __init__(self, get=None, key=None, save_raw=None, clock=utcnow):
        self._get = get or http.get
        self._key = key
        self._save_raw = save_raw
        self.clock = clock

    def key(self) -> str:
        if self._key is None:
            # The persisted user key, read by the existing reader - not new
            # key handling (CLAUDE.md).
            from props.collect import _key
            self._key = _key()
        return self._key

    def _call(self, con, sport, endpoint, url, params, estimated, now):
        """One GET, ledgered BEFORE it is made. Returns the response or raises.

        budget.reserve() checks the limits and writes the call's row at its
        estimate, committed, and only then is the request made: a database
        that cannot be written means no request (it raises, and the tick
        logs 'poll failed'). The row is filled in afterwards; if that write
        fails, the call still counts at its estimate."""
        key = self.key()
        row = budget.reserve(con, consumer="poll", endpoint=endpoint, sport=sport,
                             estimated=estimated, now=now)   # OverBudget: no request
        resp = err = None
        try:
            # One attempt for a metered call: a retry can be billed, and the
            # ledger would record only the last response's cost. The next due
            # poll is the retry.
            resp = self._get(url, params={"apiKey": key, **params},
                             label=f"{endpoint} {sport}",
                             attempts=1 if estimated else http.ATTEMPTS)
        except requests.HTTPError as e:
            err, resp = e, e.response
        except requests.RequestException as e:
            err = e
        h = resp.headers if resp is not None else {}
        cost = h.get("x-requests-last")
        rem, used = h.get("x-requests-remaining"), h.get("x-requests-used")
        budget.settle(con, row, ok=err is None,
                      cost=int(cost) if str(cost).isdigit() else None,
                      remaining=int(rem) if str(rem).isdigit() else None,
                      used=int(used) if str(used).isdigit() else None,
                      note=None if err is None else http.redact(err)[:200])
        if rem is not None:
            con.execute("INSERT INTO api_usage (ts, sport, endpoint, remaining, used)"
                        " VALUES (?,?,?,?,?)",
                        (db.utc_now(), sport, f"scanner/{endpoint}",
                         int(rem) if str(rem).isdigit() else None,
                         int(used) if str(used).isdigit() else None))
        con.commit()
        if err is not None:
            status = getattr(resp, "status_code", None)
            if status in (401, 429):
                raise Fatal(f"The Odds API said {status} on {endpoint} {sport}: "
                            f"{http.redact(err)[:120]}")
            raise err
        return resp

    def events(self, con, sport: str, now) -> list[dt.datetime]:
        """Sorted start times of upcoming events. Free."""
        key = config.SPORTS[sport]["odds_key"]
        r = self._call(con, sport, "events", f"{API}/sports/{key}/events", {},
                       0, now)
        starts = [parse_utc(e.get("commence_time")) for e in r.json()]
        return sorted(s for s in starts if s is not None)

    def poll(self, con, sport: str, now) -> dict:
        """One sport-wide odds call, stored. Checked against every limit first
        (in _call, with its ledger row)."""
        from scanner.venues import sportsbook
        est = budget.call_cost()
        key = config.SPORTS[sport]["odds_key"]
        r = self._call(con, sport, "odds", f"{API}/sports/{key}/odds",
                       {"bookmakers": ",".join(config.ODDS_BOOKS),
                        "markets": ",".join(config.ODDS_MARKETS),
                        "oddsFormat": "american"}, est, now)
        raw = None
        if self._save_raw is not None:
            raw = self._save_raw("scanner-odds", sport, r.text)
        got = sportsbook.write(con, sport, r.json(), self.clock(),
                               raw_ref=None if raw is None else str(raw))
        con.commit()
        got["cost"] = r.headers.get("x-requests-last")
        got["remaining"] = r.headers.get("x-requests-remaining")
        return got


# ---------------------------------------------------------------- poller ---

class Poller:
    """One loop, all sports. Every clock is injectable, so tests drive time."""

    def __init__(self, source, sports=None, connect=None, clock=utcnow,
                 sleep=time.sleep, out=print):
        self.source = source
        self.sports = list(sports or config.POLL_SPORTS)
        self.connect = connect or db.connect
        self.clock, self.sleep, self.out = clock, sleep, out
        self.schedule: dict = {}
        self.schedule_at = None
        self.level = None
        self.level_at = None
        self.last_poll: dict = {}
        self.state = "starting"
        # The commit this process's code came from. Read once: after a pull
        # the checkout's HEAD moves on, but the code already imported does
        # not, and ensure()'s restart-on-new-code compares against this.
        self.code_sha = db.code_sha()

    # -- state that survives a restart comes from the ledger, not memory
    def _last_polls(self, con) -> dict:
        out = {}
        for sp in self.sports:
            ts = con.execute("SELECT MAX(ts) FROM credit_ledger WHERE consumer='poll'"
                             " AND endpoint='odds' AND sport=?", (sp,)).fetchone()[0]
            out[sp] = parse_utc(ts) if ts else None
        return out

    def refresh_schedule(self, con, now):
        for sp in self.sports:
            try:
                self.schedule[sp] = self.source.events(con, sp, now)
            except Fatal:
                raise
            except Exception as e:                   # keep the old schedule
                self.out(f"  [{sp}] events list unavailable: {http.redact(e)[:120]}")
        self.schedule_at = now

    def choose_level(self, con, now):
        s = budget.status(con, now)
        self.level = budget.choose_level(self.schedule, now, s["left_today"],
                                         self.last_poll)
        self.level_at = now
        return s

    def tick(self, now=None) -> list[str]:
        """Poll whatever is due. Returns the sports polled."""
        now = now or self.clock()
        con = self.connect()
        try:
            if not self.last_poll:
                self.last_poll = self._last_polls(con)
            if self.schedule_at is None or now - self.schedule_at >= SCHEDULE_EVERY:
                self.refresh_schedule(con, now)
            if self.level_at is None or now - self.level_at >= LEVEL_EVERY:
                self.choose_level(con, now)
            if self.level is None:
                self.state = "paused: today's credits cannot cover even the last level"
                return []
            polled = []
            for sp in self.sports:
                if not budget.due(self.schedule.get(sp, []), now,
                                  self.last_poll.get(sp), self.level):
                    continue
                try:
                    got = self.source.poll(con, sp, now)
                except budget.OverBudget as e:
                    if e.limit == "pace":
                        self.state = f"waiting: {e}"
                        self.level = None
                        return polled
                    raise Fatal(str(e), kind="budget")
                except Fatal:
                    raise
                except Exception as e:
                    self.out(f"  [{sp}] poll failed: {http.redact(e)[:160]}")
                    self.last_poll[sp] = now          # do not hammer a failing feed
                    continue
                self.last_poll[sp] = now
                polled.append(sp)
                self.out(f"  {now:%H:%M:%S} [{sp}] {got['prices']} prices,"
                         f" {got['markets']} markets, {got.get('cost')} credit(s),"
                         f" {got.get('remaining')} left  (level {self.level})")
            self.state = "running"
            return polled
        finally:
            con.close()

    def beat(self, now, stop_kind=None):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        HEARTBEAT.write_text(json.dumps({
            "pid": os.getpid(), "beat_at": canon_ts(now), "state": self.state,
            "stop_kind": stop_kind,
            "level": self.level, "code_sha": self.code_sha,
            "last_poll": {k: (canon_ts(v) if v else None)
                          for k, v in self.last_poll.items()}}, indent=1),
            encoding="utf-8")

    def run(self, max_ticks: int | None = None) -> int:
        STOP.unlink(missing_ok=True)
        n = 0
        while max_ticks is None or n < max_ticks:
            now = self.clock()
            if STOP.exists():
                self.state = "stopped: asked to"
                self.beat(now, stop_kind="asked")
                STOP.unlink(missing_ok=True)
                return 0
            # Beats are stamped when written, not with the tick's start: a
            # tick on a hanging server lasts minutes.
            try:
                self.tick(now)
            except Fatal as e:
                self.state = f"stopped: {e}"
                self.beat(self.clock(), stop_kind=e.kind)
                self.out(f"STOPPED: {e}")
                return 1
            except Exception as e:                 # a bug must not end the loop
                self.out(f"  tick failed: {type(e).__name__}: {http.redact(e)[:160]}")
            self.beat(self.clock())
            n += 1
            if max_ticks is None or n < max_ticks:
                self.sleep(TICK_S)
        return 0


# ------------------------------------------------------------ supervisor ---

def heartbeat() -> dict | None:
    try:
        return json.loads(HEARTBEAT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _flock(f, take: bool) -> bool:
    """Take (without waiting) or let go of an exclusive OS lock on f's first
    byte. False if another open of the file - another loop - holds it."""
    try:
        if sys.platform == "win32":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK if take else msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), (fcntl.LOCK_EX | fcntl.LOCK_NB) if take else fcntl.LOCK_UN)
        return True
    except OSError:
        return False


def take_lock():
    """The one-loop lock, held for the life of `poll --live`: the open file,
    or None if another loop holds it. The OS lets go when the process ends,
    however it ends - so no stale lock, and no process id to probe."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    f = open(LOCK, "a+b")
    if _flock(f, True):
        return f
    f.close()
    return None


def locked() -> bool:
    """Does a running loop hold the lock? Creates nothing."""
    try:
        f = open(LOCK, "rb")
    except OSError:
        return False
    with f:
        return not (_flock(f, True) and _flock(f, False))


def alive(hb=None, now=None) -> bool:
    """A fresh heartbeat, or failing that the lock a live loop holds - which
    covers a tick slower than ALIVE_WITHIN and the first tick before any
    beat."""
    hb = heartbeat() if hb is None else hb
    if hb and str(hb.get("state", "")).startswith(("running", "waiting", "paused", "starting")):
        beat = parse_utc(hb.get("beat_at"))
        if beat is not None and (now or utcnow()) - beat <= ALIVE_WITHIN:
            return True
    return locked()


def _spawn():
    """Start `run_daily.py poll --live` detached, logging to logs/poll.log."""
    LOG.parent.mkdir(exist_ok=True)
    log = open(LOG, "a", encoding="utf-8")
    args = [sys.executable, str(ROOT / "run_daily.py"), "poll", "--live"]
    kw = dict(cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
              stdin=subprocess.DEVNULL, close_fds=True)
    if sys.platform == "win32":
        flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
        try:
            # Outlive the scheduled task that started it, if its job allows.
            return subprocess.Popen(args, creationflags=flags
                                    | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kw)
        except OSError:
            return subprocess.Popen(args, creationflags=flags, **kw)
    return subprocess.Popen(args, start_new_session=True, **kw)


def _not_of_record() -> str | None:
    """Why this data folder may not run the loop, or None if it may."""
    if RECORD.exists():
        return None
    return (f"this data folder is not the ledger of record ({RECORD} does not"
            " exist). The credit limits count only the ledger in the folder the"
            " loop runs against, and every checkout spends the same paid key, so"
            " the loop runs only against production's data folder. The owner"
            " creates that file there, by hand, in the step that turns polling on.")


def ensure(spawn=_spawn, now=None, wait=time.sleep) -> str:
    """Start the loop if it should run and is not; restart it on new code.

    A loop that stopped itself stays stopped unless the reason is gone: a
    spent budget is retried once the limits allow a call again; a refused key
    or an exhausted account ('api') waits for a person.

    Switched off, it starts nothing, and asks a loop still running (started
    while polling was on - it never rereads the flag) to stop.
    """
    hb = heartbeat() or {}             # none yet while a loop's first tick runs
    if not config.POLLING_ENABLED:
        if alive(hb, now):
            STOP.parent.mkdir(parents=True, exist_ok=True)
            STOP.write_text("polling switched off", encoding="utf-8")
            return ("polling is switched off (config.POLLING_ENABLED = False);"
                    f" asked the running loop (pid {hb.get('pid')}) to stop")
        return ("polling is switched off (config.POLLING_ENABLED = False);"
                " nothing started")
    if hb and str(hb.get("state", "")).startswith("stopped:") \
            and hb.get("stop_kind") in ("api", "budget"):
        if hb["stop_kind"] == "api":
            return (f"NOT restarted - {hb['state']}. Fix the cause, then run"
                    f" `python run_daily.py poll --live` once by hand.")
        con = db.connect()
        try:
            budget.check(con, budget.call_cost(), now)
        except budget.OverBudget as e:
            return f"NOT restarted - still over budget: {e}"
        finally:
            con.close()
    if alive(hb, now):
        if hb.get("code_sha") and hb["code_sha"] != db.code_sha():
            STOP.parent.mkdir(parents=True, exist_ok=True)
            STOP.write_text("new code", encoding="utf-8")
            for _ in range(12):                       # up to ~2 minutes
                wait(10)
                if not STOP.exists():
                    break
            else:
                # Mid-way through a long tick. A new loop now would be
                # refused, or run beside it; it stops at its next check.
                return (f"NOT restarted - the old loop (pid {hb.get('pid')}) has"
                        " not stopped yet; it stops at its next check, and the"
                        " next scheduled run starts the new code")
            why = _not_of_record()
            if why:
                return f"NOT restarted - {why}"
            p = spawn()
            return f"restarted on new code {db.code_sha()} (pid {p.pid})"
        return f"running (pid {hb.get('pid')}, level {hb.get('level')})"
    why = _not_of_record()
    if why:
        return f"NOT started - {why}"
    p = spawn()
    return f"started (pid {p.pid}); log: {LOG}"


def live() -> int:
    if not config.POLLING_ENABLED:
        print("REFUSED: polling is switched off (config.POLLING_ENABLED = False)."
              " Turning it on is a deliberate commit - see COMMANDS.md.")
        return 2
    why = _not_of_record()
    if why:
        print(f"REFUSED: {why}")
        return 2
    lock = None if alive() else take_lock()        # held until this process ends
    if lock is None:
        print("REFUSED: a polling loop is already running (a fresh heartbeat,"
              f" or it holds {LOCK}).")
        return 2
    db.init()
    from ingest.raw import save_raw
    print(f"polling {', '.join(config.POLL_SPORTS)} - {budget.call_cost()} credits a call")
    return Poller(OddsSource(save_raw=save_raw)).run()


def show_status() -> int:
    hb = heartbeat()
    now = utcnow()
    print(f"polling enabled: {config.POLLING_ENABLED}")
    if hb:
        age = now - (parse_utc(hb.get("beat_at")) or now)
        print(f"heartbeat: {hb.get('state')}  ({age.total_seconds() / 60:.0f} min ago,"
              f" level {hb.get('level')}, pid {hb.get('pid')})")
        for sp, ts in (hb.get("last_poll") or {}).items():
            print(f"  last {sp} poll: {ts or 'never'}")
    else:
        print("heartbeat: none - the loop has never run here")
    con = db.connect()
    try:
        have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "credit_ledger" not in have:
            print("credits: no ledger yet (run `python db.py`)")
            return 0
        s = budget.status(con, now)
    finally:
        con.close()
    if s["brief_active"]:
        print(f"brief: {s['brief_spent']:,} of {s['brief_cap']:,} spent,"
              f" {s['brief_days_left']} polling day(s) left")
    print(f"month: {s['month_spent']:,} of {s['month_budget']:,}")
    print(f"today: {s['today_spent']} of {s['allowance_today']:.0f} allowed"
          f" ({s['cost_per_call']} a call)")
    return 0


def main(argv) -> int:
    if "--live" in argv:
        return live()
    if "--ensure" in argv:
        print(f"poll: {ensure()}")
        return 0
    if "--status" in argv:
        return show_status()
    budget.plan()
    return 0
