# Commands

Everything runs from `C:\Users\BromC\sports-machine`.

To get a terminal there: open the folder in File Explorer, click the address
bar, type `powershell`, press Enter.

---

## The four you'll actually use

| Command | What it does |
|---|---|
| `python dashboard.py` | Builds the visual page and opens it. Add `--png` for a shareable image. |
| `python run_daily.py picks` | Tonight's games in the terminal — model vs bookmakers. |
| `python audit.py` | 100 checks. All green = everything is sound. |
| **`machine_daily.bat`** | **Double-click.** Pulls what the cloud collected into your local database, then backs it up. |

---

## Weekly-ish

```
python run_daily.py refresh
```

Tops up Statcast and retrains from scratch. Takes a few minutes. The rolling
features are only as current as the Statcast file, so this is what keeps the
predictions honest — but a ridge fit over 10,000 games barely moves in a week,
so there is no need to do it daily.

**In this folder it has a side effect.** Retraining re-records gate 1 in
`validation.json`. The scheduled job only throws away changes the machine
makes on its own (see [It also runs itself](#it-also-runs-itself)), so after a
`refresh` here its next run **refuses** until you run
`git checkout -- validation.json`. That keeps the newly trained model;
`validation.json` just goes back to the gate-1 numbers from the previous
training run.

---

## Backups

Backups are taken by `machine_daily.bat` (every time you double-click it) and
by `python backup.py`. **The scheduled job does not take one**, so if you never
double-click `machine_daily.bat`, nothing is backed up. To take one by hand, or
to check:

```
python backup.py            take one, verify it, prune to the last 14
python backup.py --list     what exists and how old it is
python backup.py --restore <file>   put one back (asks first)
```

They go to `OneDrive\sports-machine-backups` — off this disk, which is the
point. Set `SPORTS_MACHINE_BACKUP_DIR` to put them elsewhere.

Every backup is reopened, integrity-checked and row-counted (all nineteen
tables) before it is kept, and one that fails is deleted rather than left
looking like protection. `python audit.py` also fails if the newest backup is
over 7 days old.

`data/` is the only thing that exists nowhere else, and most of it can be
rebuilt — odds and games from `archive/`, Statcast from `backfill.py`, the
models by retraining. What cannot be rebuilt is your `bets` table.

---

## Occasional

```
python run_daily.py backup      same as backup.py, from the usual command
python run_daily.py finals      fetch yesterday's and today's results. Settles nothing
python run_daily.py predict     statcast top-up + today's predictions
python run_daily.py paper       place, settle and score paper bets (gate 2)
python run_daily.py market      score every finished game against the close. Clears no gate
python run_daily.py grade       finals, then settle + score paper bets, then the bet review
python run_daily.py morning     schedules + odds + features + predictions. COSTS CREDITS
python run_daily.py close       closing odds only. COSTS CREDITS
python model/validation.py      which betting gates are passed, and why
python healthcheck.py           writes STATUS.md, exits non-zero if unhealthy (the cloud's check)
python db.py                    create or upgrade the database (safe to re-run)
```

`run_daily.py` needs a mode. With none, or a word it does not know, it prints
the list of modes and does nothing — it no longer falls back to `morning`.

`paper` and `grade` rewrite `validation.json`, and `healthcheck` rewrites
`STATUS.md`. The scheduled job discards those two changes by itself, so running
them here does not stop its next run. (`refresh` is different — see above.)

---

## Checking on the cloud robot

```
python run_daily.py cronstatus
```

Free, reads only. Answers both questions at once: **did the crons fire, how
late were they,** and **did the pull catch prices before first pitch.**

What good looks like — runs a few minutes late, and every pull at 100%
pregame:

```
Recent scheduled runs
  fired                  was due           late  result
  Wed 10:19 AM ET        10:13 AM ET         6m  success

Pregame capture - did the pull beat first pitch?
  Wed 5:14 PM ET         14/14 pregame (100%)
```

A pull that lands after first pitch returns **in-play** prices — a market that
already knows part of the score. Those are worthless as a betting benchmark,
so anything under 100% is real signal that the schedule needs moving earlier.

It counts only runs of the daily workflow (`daily.yml`), not the test runs
every push triggers. Runs that fired before `daily.yml` was last changed — any
change to that file, not only to the cron times — show `(old schedule)`
instead of a made-up lateness.

### It also runs itself

A Windows scheduled task, **SportsMachine-CronStatus**, runs **twice a day**
and writes to `logs\cronstatus-latest.txt`, plus a dated copy of each run as
`logs\cronstatus-YYYYMMDD-HH.txt` (HH is the hour, so the 11:30 and 10pm copies
no longer overwrite each other). Open them any time — you do not have to be at
the computer when it runs.

Both runs do the same steps, in order: merge what the cloud collected
(`merge_archive`), fetch results (`finals`), build predictions (`predict`),
`paper` (place any paper bet that is due, settle finished ones, re-score
gate 2), `market`, `cronstatus`, `poll --ensure` (a no-op while the scanner's
loop is switched off), and the health checks (`monitor.py`).

| Time | In practice |
|---|---|
| 11:30 am | no game has started, so this is when paper bets get **placed**, at the newest prices that have arrived — the cloud's 10:13 morning pull is often still hours late at 11:30 (on 9/23 it fired at 2:22 pm), so these can be the previous evening's. Last night's games get settled |
| 10:00 pm | most of the day's games are over, so this one mostly **settles** |

None of these steps costs API credits and no real bet is ever placed. (The
scanner's polling loop, which `poll --ensure` starts once it is switched on,
does spend credits — within its limits; see the scanner section below.)

**Before it does anything**, it throws away the changes the machine makes to
tracked files on its own: `STATUS.md`, and `validation.json` when only its
gate-2 block changed (the `paper` step recomputes that from the database
minutes later). Any other uncommitted change in the folder makes it **refuse**:
nothing is pulled, bet or settled; `logs\cronstatus-latest.txt` says
**REFUSED TO RUN** and lists the files; `ALERTS.md` gets an ERROR; a desktop
alert pops up; and Task Scheduler records the run as failed (exit code 1). It
runs from a copy of itself in `%TEMP%`, so the `git pull` it does cannot
scramble the file while it is running.

**Why placing has to happen on your machine:** a paper bet needs the trained
model and the Statcast file, both of which live in `data/` — gitignored, and
it has to stay that way because the repo is public. The cloud runner literally
cannot do it; its log says *"no predictions: No Statcast parquet"*. So gate 2
can only ever be fed from this PC.

If your PC is off or asleep, the run happens the next time you log in rather
than skipping the day. A late catch-up places at a later (worse) price, which
makes gate 2 harder rather than easier — the safe direction.

To check on it, or remove it:

```
schtasks /Query /TN SportsMachine-CronStatus
schtasks /Delete /TN SportsMachine-CronStatus /F
```

To change the times, note that the task has **two** triggers (11:30am and
10pm). `schtasks /Change` takes a single `/ST` start time, so it cannot set
both. In PowerShell, give it both at once — this replaces the old pair, so put
the times you want in each line:

```powershell
$t1 = New-ScheduledTaskTrigger -Daily -At 11:30am
$t2 = New-ScheduledTaskTrigger -Daily -At 10:00pm
Set-ScheduledTask -TaskName SportsMachine-CronStatus -Trigger $t1, $t2
```

Or open **Task Scheduler** from the Start menu, find it in the top-level list,
and edit the Triggers tab. It runs `scheduled_check.bat`, which pulls the repo
first so the report reflects what the cloud actually collected.

### The raw version, if you want it:

```
gh run list --workflow daily.yml --limit 5
```

Without `--workflow daily.yml` the list is mostly test runs from pushes, and
the scheduled pulls can be missing from it entirely.

Look at the **trigger column** (5th from left):

- `schedule` — a cron fired on its own. This is what you want to see.
- only `workflow_dispatch` / `push` — nothing has fired automatically yet.

To run it by hand, use the **Run workflow** button on GitHub and pick a mode:
`morning`, `close`, or `grade`. `morning` and `close` spend the cloud key's
credits; `grade` pulls no odds.

---

## Rare

```
python backfill.py              full history download — hours, resumable
python backfill.py topup        just the days since the last download
python merge_archive.py         what machine_daily.bat does, manually
python merge_archive.py --all   re-read every archive file (repair/rebuild)
python features/build_training.py   rebuild the training table + retrain + save.
                                Changes validation.json, like refresh
python resolve_market_close.py  materialise the de-vigged close for every game
python backfill_nfl.py          nflverse schedules and EPA, free
python backfill_odds_history.py historical odds. Plans only, unless given
                                --execute AND --max-credits. COSTS 10 credits per request
python export_snapshots.py      the cloud job's export. REFUSES to run here
```

`merge_archive.py --all` puts every row through the same checks live ingest
uses, so an impossible row in an old file (a 0-0 final, say) is rejected
rather than resurrected, and a finished game's score is never overwritten.

`resolve_market_close.py` is the only thing that fills `market_close`, the
closing prices gate 1 is judged on; nothing runs it automatically. It adds
games not yet resolved and leaves existing rows alone, unless given
`--rebuild`.

`export_snapshots.py` writes every row of whatever database it is pointed at
into `archive/`. On the cloud runner that is one fresh pull; here it would be
the whole local database, into a public repo — which happened once, on
2026-09-24. It refuses outside GitHub Actions. `--local` overrides that; don't.

### Your own bets (the scoreboard)

```
python run_daily.py bet --sport nfl --date 2026-10-04     --game "Chiefs at Raiders" --market h2h --side "Kansas City Chiefs"     --price -215 --book draftkings --stake 25 --tag research
python run_daily.py bet --csv bets_inbox/     every .csv in that folder, one bet per row
python run_daily.py scoreboard                grade them and report
```

Records a bet you placed anywhere and grades it the way the model's paper bets
are graded. A moneyline (`--market h2h`) bet gets all of it: fair close, EV
after vig, the shop/info split, coverage, and the same three-gate verdict, with
its result taken from the final score. Spreads, totals and props get none of
the price analysis — only moneyline prices are stored — and are settled only
from a result you type in (`--result win`, `loss` or `push`). Entry refuses a
game that has started, and there is no command yet to add a result later, so
in practice a spread, total or prop bet cannot be settled at the moment.
Tags: `boost`, `promo`, `research`, `sgp-leg`.

`--game` is matched against that date's games by team words ("Chiefs at
Raiders"). Each real game is stored once per feed; the score feed's row is the
one used. Two games that both match — a doubleheader — are refused; give the
game id instead.

A CSV for `--csv` needs a header row with the columns
`sport,date,game,market,side,price,book,stake`, and may add `prop_line`,
`player`, `tag` and `manual_result`. A row it cannot use is printed as
`REFUSED` with its file and line number; the rest are recorded.

It refuses a game it cannot resolve, a game that has already started, an
impossible price, a moneyline side that does not name exactly one of the two
teams (or `home`/`away`), a spread or total with no `--prop-line`, and a prop
with no player. It never stakes money.

### Sharp vs soft (the control)

```
python run_daily.py shop                what the collected prices flag, free
python run_daily.py shop --historical   plus the E4 cross-check on the archive
```

Flags any book paying more than 1.5% EV against Pinnacle's de-vigged price.
It is +EV by construction, so a negative result over a real sample means the
fair-line or grading code is broken — that is its main job. It never stakes
money and never feeds a gate.

### The scanner's polling loop (brief of 2026-09-25)

```
python run_daily.py poll --plan      the credit arithmetic: what each cadence costs. Free, no calls
python run_daily.py poll --status    is the loop running, what has it spent. Free, no calls
python run_daily.py poll --ensure    start it if it should be running (the scheduled job runs this). Free
python run_daily.py poll --live      run the loop in this window. COSTS CREDITS
```

**It is switched off**, and stays off until Phase C of the brief turns it on
with a commit: `POLLING_ENABLED = False` in `config.py`. While it is off,
`--ensure` does nothing and `--live` refuses.

When it is on, it asks The Odds API for every NFL, NBA and NHL game's prices
(h2h, spreads, totals; Pinnacle plus nine NJ books) at 3 credits a call, as
often as the budget allows — up to every 2 minutes near a start, less often
further out, never once a game has started. It spends the **paid** key. Before
every call it checks three limits in code: the brief's **6,000 credits in
total**, **12,000 a month**, and a daily pace so one day cannot spend the lot.
`--plan` shows the arithmetic: the brief's own cadence would cost ~55,600 a
month, so the loop slows down in fixed steps, and even at its slowest every game
still gets a price in its last 30 minutes. A refused key or an exhausted
account stops it until you run `--live` once by hand.

### The scanner's strategies and their gates

```
python run_daily.py strategies          every strategy, with its gate verdict. Free
python run_daily.py strategies --score  re-test each one's gate 2. Free
python run_daily.py scoreboard          your bets, then the scanner: per strategy, per venue, in total
```

There are none yet: the brief adds them in Phases B and E, each only after
its entry in `docs/experiments.md` is written. Each strategy has its own
block in `validation.json`, held to the same three gates as a sport — its own
backtest, its own 50+ graded paper positions at 3 standard errors with
coverage and a placebo, and your own `arm("<name>")`. Gate 2 is re-tested at
most twice a day per strategy. Every order is paper: the database itself
refuses any other kind, and `python audit.py` checks no code can send one.

### Collection (Phase 0)

```
collect_props.bat               NFL/NBA props + game lines, five times a day. COSTS CREDITS
python props/collect.py --plan  what it would pull and what it would cost. Free, makes no calls
python props/collect.py --run --sport nfl    one pull. COSTS CREDITS, honours the monthly cap
```

`collect_props.bat` is what Task Scheduler runs as `SportsMachine-Collect`, at
11:45am, 3:45, 6:00, 8:00 and 9:00pm. It appends everything it prints to
`logs\collect.log`. The task always reports success, so that file is the only
place a failed collection shows.

It spends the **paid** key, which it reads straight from your Windows user
environment — so it spends even from a window where you cleared
`ODDS_API_KEY`. It stops before any request that would take the calendar
month's total past 3,000 credits. That total is read from
`data\props_live\requests.jsonl` in the folder it runs from, so production and
dev each keep their own count.

An idle run costs nothing: the events list is free, and props are pulled only
for games inside their lead window that have not already been recorded.

### Checking the machine agrees with itself

```
python -m pytest tests/ -q          includes tests/test_entry_points.py: every
                                    run_daily mode documented here, every module
                                    reachable from an entry point (CI runs it)
python tests/golden/capture.py      prove behaviour has not moved (dev only:
                                    needs data_golden/)
```

---

## What costs API credits

There are **two Odds API keys**, billed separately.

**The cloud's key** is the `ODDS_API_KEY` repo secret on GitHub, on the free
tier of **500 credits a month**. It is spent by every cloud run in `morning` or
`close` mode — the three daily crons, plus any you start by hand with the Run
workflow button. One pull costs **one credit per in-season sport**. The only
place to see its balance is the run log's `Credits left:` line on GitHub (480
on 2026-09-24); nothing on this PC records it.

**The paid key** is `ODDS_API_KEY` in your Windows user environment (100,000
credits; 41,742 left on 2026-09-24). It is spent by:

- `collect_props.bat` / `python props/collect.py --run` — five times a day by
  the `SportsMachine-Collect` task, capped by the script at 3,000 a month
- `python run_daily.py morning` or `close`, if you run them on this PC
- `python ingest/odds.py`
- `python backfill_odds_history.py --execute` — 10 credits per request
- the scripts in `research/b3/`
- `python run_daily.py poll --live`, and the loop the scheduled job starts
  once `POLLING_ENABLED` is on — 3 credits a call, within the limits above

`python monitor.py`'s credit line reports this key, not the cloud's.

**Free:** `picks`, `audit`, `dashboard`, `grade`, `refresh`, `validation`,
`healthcheck`, `backup`, `cronstatus`, `paper`, `predict`, `finals`, `market`,
`shop`, `bet`, `scoreboard`, `machine_daily.bat`, `props/collect.py --plan`,
and anything reading the database.

`refresh` is free but slow — it re-downloads Statcast and retrains, and
touches no odds API.

The three daily cloud pulls are projected at **372 credits in October**
(4 sports in season × 3 pulls × 31 days), the tightest month. **Do not add a
fourth daily pull without redoing that arithmetic** — the maths is written into
`.github/workflows/daily.yml`, which rounds it to ~360.

---

## The cloud schedule

Runs on GitHub's servers three times a day. Your computer can be off.

| Your time (Eastern) | Mode |
|---|---|
| 10:13 am | opening odds |
| 5:09 pm | closing odds — ~2h before the 7:05 slate |
| 7:51 pm | closing odds — ~2h before west-coast games |

Times are set in UTC, so in winter each fires an hour earlier on your clock.

**Why so early?** GitHub runs scheduled jobs on shared machines and starts them
late — measured on this repo, by anywhere from a few minutes to four hours. A
pull aimed at 25 minutes before first pitch lands *after* first pitch on a bad
day, and what comes back is in-play pricing, which is worthless as a betting
benchmark. Two hours early still catches a real pregame market.

So don't be alarmed if a run shows up an hour or two after the time above.
That's expected, and the schedule is built to absorb it.

---

## Is anything wrong?

Open **`ALERTS.md`** in the project folder. Every scheduled run rewrites it —
including a run that refuses to start, which writes an ERROR saying why. Its
first line says when it was written: if that is more than half a day old, the
scheduled job has not run since (PC off, or the task did not start), and
*All clear* only means all was clear then.

Anything serious also pops a **desktop notification**.

```
python monitor.py              re-check right now
python monitor.py --tail 40    what it has found recently
python notify.py               send a test alert, to prove it reaches you
```

`notify.py` reports whether each channel actually delivered, and it leaves
`ALERTS.md` holding its test alert until the next scheduled run rewrites it.

**Want it on your phone?** One command, no account and no password:

```
setx SPORTS_MACHINE_NTFY_TOPIC "sportsmachine-pick-something-random"
```

Then install the **ntfy** app and subscribe to that same topic. Nothing leaves
this computer until you set that variable, and clearing it turns it off again.

Pick something long and unguessable — a free ntfy topic has no password, so
anyone who knows the name can read it. The messages are things like
*"3 games unsettled"*, not anything private, but it is worth doing properly.

---

## When something looks wrong

**`No such file or directory`** — you are in the wrong folder.
Run `cd C:\Users\BromC\sports-machine` and try again.

**`machine_daily.bat` refuses to run** — you have uncommitted edits. It tells
you what to do; nothing was changed.

**`ALERTS.md` or the log says REFUSED TO RUN** — the same cause: an edit in
this folder that the scheduled job will not throw away. `git status --short`
lists it. See [docs/production-setup.md](docs/production-setup.md).

**The dashboard looks stale** — it reads the database directly, so rerun
`python dashboard.py`. If the *data* is stale, run `refresh` first (and see
[Weekly-ish](#weekly-ish) for what that does to the next scheduled run).

**Anything at all** — `python audit.py` is the fastest way to find out whether
the problem is real.

---

## The two-page overview

If you want the whole picture - what runs when, what each gate says, what has
been tested and what is still open - read
[docs/state-of-the-machine.md](docs/state-of-the-machine.md).

## Where things live

```
data/            database, Statcast, trained model   (not on GitHub — back this up)
archive/         odds CSVs the cloud commits          (on GitHub)
validation.json  which sports may bet, and why        (on GitHub)
STATUS.md        the cloud healthcheck's scoreboard   (on GitHub)
ALERTS.md        the local health checks' verdict     (this PC only)
logs/            the scheduled job's reports, collect.log, health.jsonl (this PC only)
dashboard.html   generated — rebuild any time
dashboard.png    generated — the shareable image
```

`data/` is the only thing that exists nowhere else. Everything else is on
GitHub or regenerates itself.
