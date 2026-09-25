"""Odds API credits: the ledger, the limits, and the polling plan.

    python run_daily.py poll --plan      the arithmetic, from the settings. Free.

WHAT A CALL COSTS. The Odds API bills markets x regions, and a named book
list bills one region per ten books (v4 docs, and the cloud's own run logs:
Pinnacle plus three books, one market, costs 1). config.ODDS_BOOKS names ten,
config.ODDS_MARKETS three, so one sport-wide call costs 3 credits. The events
list, which says when every game starts, is free.

THREE LIMITS, checked BEFORE every metered call (check(), inside reserve(),
which also writes the call's ledger row before the request), from the ledger:

  brief    everything the scanner has spent must stay within
           config.CREDIT_CAP_BRIEF while config.BRIEF_ACTIVE
  month    this ET calendar month within config.POLL_MONTHLY_BUDGET
  today    a daily pace: what is left of the brief (or the month, whichever
           is tighter) divided by the polling days left. Without it, a bug or
           a busy weekend could spend the whole brief in one day. Unspent
           credits roll forward.

A call that fails before a response counts at its estimate, because a limit
that trusts a missing number is not a limit.

THE CADENCE (the brief's, A2): by the time to the soonest game not yet
started - within 90 min every 2 min; 90 min to 6 h every 5; 6-24 h every 15;
beyond that hourly; once everything has started, nothing. One call returns a
sport's whole slate, so the soonest game sets the pace for the sport.

That cadence costs about 786 credits a day for one NBA-like sport - about
23,600 a month - so it cannot run on 6,000 or on 12,000 a month. When the
budget cannot cover it, the governor steps down a fixed LADDER, slowing the
far-off tiers first and the closing tier last. Every level that polls at all
still polls every sport at least every 30 minutes inside the last 90 before a
start, so every game gets a price inside CLOSING_WINDOW_MIN (60) of its first
pitch - the price gate 2's grading needs. If even the last level does not
fit, polling pauses until the next day's allowance.
"""
import bisect
import datetime as dt
import math

import config
from feeds import ET, parse_utc
from scanner.store import canon_ts

REGION_BOOKS = 10

# Minutes to the soonest unstarted game at which each tier begins.
TIER_EDGES = (90, 360, 1440)
TIERS = ("closing", "near", "day", "far")

# Poll interval in minutes per tier, fastest first. None: do not poll then.
LADDER = [
    (2, 5, 15, 60),          # 0  the brief's cadence
    (5, 10, 30, 120),
    (10, 20, 60, 240),
    (15, 30, 120, None),
    (20, 60, 240, None),
    (30, 120, None, None),
    (30, None, None, None),  # last resort: closing prices only
]


class OverBudget(RuntimeError):
    """A metered call would break a limit. Nothing was requested.
    `limit` is 'brief', 'month' or 'pace' - only 'pace' clears by tomorrow."""

    def __init__(self, message, limit):
        super().__init__(message)
        self.limit = limit


def call_cost(n_markets: int | None = None, n_books: int | None = None) -> int:
    n_markets = len(config.ODDS_MARKETS) if n_markets is None else n_markets
    n_books = len(config.ODDS_BOOKS) if n_books is None else n_books
    return n_markets * math.ceil(n_books / REGION_BOOKS)


# ---------------------------------------------------------------- ledger ---

def record(con, *, consumer: str, endpoint: str, estimated: int, ok: bool,
           cost=None, remaining=None, used=None, sport=None, note=None, ts=None) -> int:
    """One row per metered (or supposedly free) call. The caller commits.
    Returns the row's id."""
    return con.execute(
        "INSERT INTO credit_ledger (ts, consumer, sport, endpoint, estimated,"
        " cost, remaining, used, ok, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (canon_ts(ts or dt.datetime.now(dt.timezone.utc)), consumer, sport,
         endpoint, int(estimated), None if cost is None else int(cost),
         None if remaining is None else int(remaining),
         None if used is None else int(used), 1 if ok else 0, note)).lastrowid


def reserve(con, *, consumer: str, endpoint: str, estimated: int, sport=None,
            now=None) -> int:
    """No ledger row, no request. check() (for a metered call) and the call's
    row at its estimate, in ONE write transaction committed BEFORE the
    request. A second loop waits for the lock and then sees this row, so two
    cannot both spend the last credits; and a request whose result cannot be
    written back still counts, at its estimate. Raises OverBudget, or the
    database's own error, with nothing requested. Returns the row's id."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if con.in_transaction:
        con.commit()
    con.execute("BEGIN IMMEDIATE")
    try:
        if estimated:
            check(con, estimated, now)
        row = record(con, consumer=consumer, endpoint=endpoint, sport=sport,
                     estimated=estimated, ok=False, note="in flight", ts=now)
        con.commit()
    except BaseException:
        con.rollback()
        raise
    return row


def settle(con, row: int, *, ok: bool, cost=None, remaining=None, used=None,
           note=None):
    """Fill in a reserve()d row once the response, or its absence, is known.
    The caller commits; until then the row counts at its estimate."""
    con.execute(
        "UPDATE credit_ledger SET cost=?, remaining=?, used=?, ok=?, note=?"
        " WHERE id=?",
        (None if cost is None else int(cost),
         None if remaining is None else int(remaining),
         None if used is None else int(used), 1 if ok else 0, note, row))


def spent(con, since=None, until=None) -> int:
    """Credits the ledger records in [since, until). A missing cost counts at
    its estimate."""
    q = "SELECT COALESCE(SUM(COALESCE(cost, estimated)), 0) FROM credit_ledger WHERE 1=1"
    args = []
    if since is not None:
        q += " AND ts >= ?"
        args.append(canon_ts(since))
    if until is not None:
        q += " AND ts < ?"
        args.append(canon_ts(until))
    return int(con.execute(q, args).fetchone()[0])


def _et_midnight(day: dt.date) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, tzinfo=ET)


def et_day(now) -> tuple[dt.datetime, dt.datetime]:
    d = now.astimezone(ET).date()
    return _et_midnight(d), _et_midnight(d + dt.timedelta(days=1))


def et_month(now) -> tuple[dt.datetime, dt.datetime, int]:
    """(start, end, days left including today) of now's ET month."""
    d = now.astimezone(ET).date()
    first = d.replace(day=1)
    nxt = (first + dt.timedelta(days=32)).replace(day=1)
    return _et_midnight(first), _et_midnight(nxt), (nxt - d).days


def days_polled(con, before) -> int:
    """Distinct ET days with a metered call before `before`."""
    days = set()
    for (ts,) in con.execute("SELECT ts FROM credit_ledger WHERE ts < ?"
                             " AND estimated > 0", (canon_ts(before),)):
        days.add(parse_utc(ts).astimezone(ET).date())
    return len(days)


def status(con, now=None) -> dict:
    """Where every limit stands. All the numbers check() and the monitor use."""
    now = now or dt.datetime.now(dt.timezone.utc)
    day0, day1 = et_day(now)
    m0, m1, month_days_left = et_month(now)
    month_before_today = spent(con, m0, day0)
    today = spent(con, day0, day1)
    month_spent = month_before_today + today
    month_pace = (config.POLL_MONTHLY_BUDGET - month_before_today) / month_days_left
    out = {"cost_per_call": call_cost(), "today_spent": today,
           "month_spent": month_spent, "month_budget": config.POLL_MONTHLY_BUDGET,
           "month_left": config.POLL_MONTHLY_BUDGET - month_spent,
           "brief_active": config.BRIEF_ACTIVE}
    pace = month_pace
    if config.BRIEF_ACTIVE:
        brief_before_today = spent(con, None, day0)
        days_left = max(1, config.BRIEF_POLL_DAYS - days_polled(con, day0))
        brief_pace = (config.CREDIT_CAP_BRIEF - brief_before_today) / days_left
        out.update({"brief_spent": brief_before_today + today,
                    "brief_cap": config.CREDIT_CAP_BRIEF,
                    "brief_left": config.CREDIT_CAP_BRIEF - brief_before_today - today,
                    "brief_days_left": days_left})
        pace = min(pace, brief_pace)
    out["allowance_today"] = max(0.0, pace)
    out["left_today"] = max(0.0, pace - today)
    return out


def check(con, estimated: int, now=None) -> dict:
    """Raise OverBudget if a call estimated at `estimated` would break any
    limit. Returns status() when it may go ahead."""
    s = status(con, now)
    if s["brief_active"] and s["brief_spent"] + estimated > config.CREDIT_CAP_BRIEF:
        raise OverBudget(f"the brief's cap: {s['brief_spent']:,} of "
                         f"{config.CREDIT_CAP_BRIEF:,} spent, this call needs {estimated}",
                         "brief")
    if s["month_spent"] + estimated > config.POLL_MONTHLY_BUDGET:
        raise OverBudget(f"this month's budget: {s['month_spent']:,} of "
                         f"{config.POLL_MONTHLY_BUDGET:,} spent, this call needs {estimated}",
                         "month")
    if s["today_spent"] + estimated > s["allowance_today"]:
        raise OverBudget(f"today's pace: {s['today_spent']} of "
                         f"{s['allowance_today']:.0f} spent, this call needs {estimated}",
                         "pace")
    return s


# --------------------------------------------------------------- cadence ---

def tier(minutes: float) -> str | None:
    """Tier for the minutes until the soonest unstarted game. None: nothing
    left to start (in play, or no games)."""
    if minutes is None or minutes <= 0:
        return None
    for edge, name in zip(TIER_EDGES, TIERS):
        if minutes <= edge:
            return name
    return TIERS[-1]


def interval(level: int, minutes: float) -> int | None:
    t = tier(minutes)
    return None if t is None else LADDER[level][TIERS.index(t)]


def soonest(starts, now) -> dt.datetime | None:
    """The first start strictly after now. `starts` must be sorted."""
    i = bisect.bisect_right(starts, now)
    return starts[i] if i < len(starts) else None


def due(starts, now, last_poll, level: int) -> bool:
    """Is a sport due a poll now, at this ladder level?"""
    s = soonest(starts, now)
    if s is None:
        return False
    iv = interval(level, (s - now).total_seconds() / 60)
    if iv is None:
        return False
    return last_poll is None or (now - last_poll).total_seconds() >= iv * 60


def poll_times(schedule: dict, start, end, level: int, last_polls=None,
               step_min: int = 1) -> dict:
    """When each sport would be polled between start and end. Pure."""
    last = dict(last_polls or {})
    schedule = {sp: sorted(starts) for sp, starts in schedule.items()}
    times = {sp: [] for sp in schedule}
    t = start
    step = dt.timedelta(minutes=step_min)
    while t < end:
        for sp, starts in schedule.items():
            if due(starts, t, last.get(sp), level):
                times[sp].append(t)
                last[sp] = t
        t += step
    return times


def project(schedule: dict, start, end, level: int, last_polls=None) -> dict:
    """Polls per sport between start and end at one ladder level."""
    return {sp: len(ts) for sp, ts in
            poll_times(schedule, start, end, level, last_polls).items()}


def choose_level(schedule: dict, now, credits_left: float, last_polls=None,
                 cost: int | None = None, end=None) -> int | None:
    """The fastest ladder level whose projected spend to the end of the ET day
    fits in credits_left. None when not even the last one does."""
    cost = call_cost() if cost is None else cost
    end = end or et_day(now)[1]
    for level in range(len(LADDER)):
        n = sum(project(schedule, now, end, level, last_polls).values())
        if n * cost <= credits_left:
            return level
    return None


# ------------------------------------------------------------------ plan ---

# A typical in-season week, in ET, for the arithmetic only - not a schedule
# the loop uses (it reads real start times from the free events list).
TYPICAL = {
    "nfl": {3: ["20:15"],                                     # Thursday
            6: ["13:00"] * 9 + ["16:05"] * 2 + ["16:25"] * 2 + ["20:20"],
            0: ["20:15"]},                                    # Monday
    "nba": {d: ["19:00", "19:00", "19:30", "20:00", "21:00", "22:00", "22:30"]
            for d in range(7)},
    "nhl": {d: ["19:00", "19:00", "19:30", "20:00", "21:00", "22:00"]
            for d in range(7)},
}


def typical_schedule(sports, monday: dt.date, weeks: int = 2) -> dict:
    out = {}
    for sp in sports:
        starts = []
        for w in range(weeks):
            for wd, times in TYPICAL[sp].items():
                day = monday + dt.timedelta(days=7 * w + wd)
                for hhmm in times:
                    h, m = map(int, hhmm.split(":"))
                    starts.append(dt.datetime(day.year, day.month, day.day, h, m,
                                              tzinfo=ET).astimezone(dt.timezone.utc))
        out[sp] = sorted(starts)
    return out


def _week_by_level(schedule, monday) -> list[list[int]]:
    """Credits per day (Mon..Sun) at each ladder level."""
    cost = call_cost()
    rows = []
    for level in range(len(LADDER)):
        days = []
        last = {}
        for d in range(7):
            s = _et_midnight(monday + dt.timedelta(days=d))
            e = s + dt.timedelta(days=1)
            n = project(schedule, s, e, level, last)
            days.append(sum(n.values()) * cost)
        rows.append(days)
    return rows


def _governed_week(schedule, monday, per_day: float) -> tuple[list, list]:
    """What the governor would pick each day of the week, and spend."""
    cost = call_cost()
    levels, spend = [], []
    for d in range(7):
        s = _et_midnight(monday + dt.timedelta(days=d))
        e = s + dt.timedelta(days=1)
        lv = choose_level(schedule, s, per_day, cost=cost, end=e)
        levels.append(lv)
        spend.append(0 if lv is None else sum(project(schedule, s, e, lv).values()) * cost)
    return levels, spend


def _describe(level) -> str:
    if level is None:
        return "paused"
    parts = []
    for name, iv in zip(TIERS, LADDER[level]):
        parts.append(f"{name} {'off' if iv is None else f'{iv} min'}")
    return f"level {level}: " + ", ".join(parts)


def plan(out=print) -> dict:
    """Print the arithmetic behind the polling budget. Makes no calls."""
    cost = call_cost()
    monday = dt.date(2026, 11, 2)                     # all three sports in season
    sports = list(config.POLL_SPORTS)
    sched = typical_schedule(sports, monday)
    out("=" * 76)
    out("POLLING BUDGET  (arithmetic only - no API calls)")
    out("=" * 76)
    out(f"\none call = {len(config.ODDS_MARKETS)} markets x "
        f"{math.ceil(len(config.ODDS_BOOKS) / REGION_BOOKS)} region "
        f"({len(config.ODDS_BOOKS)} named books) = {cost} credits;"
        f" the events list is free")
    out(f"sports: {', '.join(sports)} - a typical in-season week (see TYPICAL)")

    week = _week_by_level(sched, monday)
    out("\ncredits a day at each ladder level, Mon..Sun, all sports together")
    for level, days in enumerate(week):
        out(f"  {_describe(level):58s}")
        out(f"      {'  '.join(f'{x:5d}' for x in days)}   "
            f"~{sum(days) * 30 / 7:,.0f}/month")

    brief_day = config.CREDIT_CAP_BRIEF / config.BRIEF_POLL_DAYS
    normal_day = config.POLL_MONTHLY_BUDGET / 30.4
    result = {"cost_per_call": cost, "ideal_month": sum(week[0]) * 30 / 7}
    for label, per_day in (("THE BRIEF", brief_day), ("NORMAL OPERATION", normal_day)):
        levels, spend = _governed_week(sched, monday, per_day)
        out(f"\n{label}: {per_day:,.0f} credits a day allowed")
        for name, lv, sp in zip(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
                                levels, spend):
            out(f"  {name}  {sp:4d} credits  {_describe(lv)}")
        per_week = sum(spend)
        result[label] = {"levels": levels, "per_week": per_week}
        if label == "THE BRIEF":
            total = per_week / 7 * config.BRIEF_POLL_DAYS
            result["brief_projection"] = total
            out(f"  over {config.BRIEF_POLL_DAYS} polling days: ~{total:,.0f} of "
                f"{config.CREDIT_CAP_BRIEF:,}  "
                f"{'UNDER' if total <= config.CREDIT_CAP_BRIEF else 'OVER'} the cap")
        else:
            month = per_week * 30.4 / 7
            result["normal_month"] = month
            with_props = month + 3000
            out(f"  per month: ~{month:,.0f}, plus the props collector's 3,000 cap"
                f" = ~{with_props:,.0f} of 15,000  "
                f"{'UNDER' if with_props <= 15000 else 'OVER'}")
    out(f"\nThe brief's own cadence (level 0) would need ~{result['ideal_month']:,.0f}"
        f" a month. Every level that polls keeps the closing tier at 30 min or"
        f" faster, so each game still gets a price inside 60 min of its start.")
    return result
