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
| `python audit.py` | 86 checks. All green = everything is sound. |
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

---

## Backups

`machine_daily.bat` takes one every time you run it, so normally you do
nothing. To take one by hand, or to check:

```
python backup.py            take one, verify it, prune to the last 14
python backup.py --list     what exists and how old it is
python backup.py --restore <file>   put one back (asks first)
```

They go to `OneDrive\sports-machine-backups` — off this disk, which is the
point. Set `SPORTS_MACHINE_BACKUP_DIR` to put them elsewhere.

Every backup is reopened and row-counted before it is kept, and one that
fails is deleted rather than left looking like protection. `python audit.py`
also fails if the newest backup is over 7 days old.

`data/` is the only thing that exists nowhere else, and most of it can be
rebuilt — odds and games from `archive/`, Statcast from `backfill.py`, the
models by retraining. What cannot be rebuilt is your `bets` table.

---

## Occasional

```
python run_daily.py backup      same as backup.py, from the usual command
python run_daily.py paper       place/settle/score paper bets (gate 2)
python run_daily.py predict     statcast top-up + today's predictions
python run_daily.py morning     schedules + odds + features + predictions
python run_daily.py close       closing odds only
python run_daily.py grade       final scores + bet review
python model/validation.py      which betting gates are passed, and why
python healthcheck.py           writes STATUS.md, exits non-zero if unhealthy
python db.py                    create or upgrade the database (safe to re-run)
```

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

Runs that fired before you last edited the crons show `(old schedule)` instead
of a made-up lateness.

### It also runs itself

A Windows scheduled task, **SportsMachine-CronStatus**, runs **twice a day**
and writes to `logs\cronstatus-latest.txt`. Open that file any time — you do
not have to be at the computer when it runs.

| Time | What it does |
|---|---|
| 11:30 am | merge the cloud's morning pull, build predictions, **place** paper bets |
| 10:00 pm | merge the day's pulls, **settle** finished paper bets, score gate 2 |

Both runs also write the cron report. Nothing costs API credits and no real
bet is ever placed.

**Why placing has to happen on your machine:** a paper bet needs the trained
model and the Statcast file, both of which live in `data/` — gitignored, and
it has to stay that way because the repo is public. The cloud runner literally
cannot do it; its log says *"no predictions: No Statcast parquet"*. So gate 2
can only ever be fed from this PC.

If your PC is off or asleep, the run happens the next time you log in rather
than skipping the day. A late catch-up places at a later (worse) price, which
makes gate 2 harder rather than easier — the safe direction.

To change the time, remove it, or check on it:

```
schtasks /Query /TN SportsMachine-CronStatus
schtasks /Change /TN SportsMachine-CronStatus /ST 21:30
schtasks /Delete /TN SportsMachine-CronStatus /F
```

Or open **Task Scheduler** from the Start menu and find it in the top-level
list. It runs `scheduled_check.bat`, which pulls the repo first so the report
reflects what the cloud actually collected.

### The raw version, if you want it:

```
gh run list --limit 5
```

Look at the **trigger column** (5th from left):

- `schedule` — a cron fired on its own. This is what you want to see.
- only `workflow_dispatch` / `push` — nothing has fired automatically yet.

To run it by hand, use the **Run workflow** button on GitHub and pick a mode:
`morning`, `close`, or `grade`.

---

## Rare

```
python backfill.py              full history download — hours, resumable
python backfill.py topup        just the days since the last download
python merge_archive.py         what machine_daily.bat does, manually
python merge_archive.py --all   re-read every archive file (repair/rebuild)
python features/build_training.py   rebuild the training table + retrain + save
python run_daily.py finals      pull yesterday's finals and settle what they settle
python resolve_market_close.py  materialise the de-vigged close for every game
python export_snapshots.py      write archive CSVs (the cloud job runs this)
python backfill_nfl.py          nflverse schedules and EPA, free
python backfill_odds_history.py historical odds. COSTS 10 credits per request
```

### Collection (Phase 0)

```
collect_props.bat               NFL/NBA props + game lines, five times a day
python props/collect.py --plan  what it would pull and what it would cost, free
python props/collect.py --run --sport nfl    one pull, honours the credit cap
```

`collect_props.bat` is what Task Scheduler runs as `SportsMachine-Collect`. An
idle run costs nothing: the events list is free, and props are pulled only for
games inside their lead window that have not already been recorded.

### Checking the machine agrees with itself

```
python research/tools/integration_matrix.py          regenerate the matrix
python research/tools/integration_matrix.py --check  fail if a mode is undocumented
python research/tools/inventory.py                   dead code, prose, duplication
python research/tools/run_all_commands.py            run every command against a copy
python tests/golden/capture.py                       prove behaviour has not moved
```

---

## What costs API credits

The Odds API free tier is **500 credits a month**, and one pull costs
**one credit per in-season sport**.

**Spends credits:** `morning`, `close`, and every cloud cron run. Nothing else.

**Free:** `picks`, `audit`, `dashboard`, `grade`, `refresh`, `validation`,
`healthcheck`, `backup`, `cronstatus`, `paper`, `predict`, `machine_daily.bat`,
and anything reading the database.

`refresh` is free but slow — it re-downloads Statcast and retrains, and
touches no odds API.

The three daily cron runs cost about 360 credits in October, when all four
sports overlap. That is the tightest month. **Do not add a fourth daily pull
without redoing that arithmetic** — the maths is written into
`.github/workflows/daily.yml`.

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

Open **`ALERTS.md`** in the project folder. It is rewritten every time the
scheduled job runs, so it is never stale — if it says *All clear*, it is.

Anything serious also pops a **desktop notification**.

```
python monitor.py              re-check right now
python monitor.py --tail 40    what it has found recently
python notify.py               send a test alert, to prove it reaches you
```

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

**The dashboard looks stale** — it reads the database directly, so rerun
`python dashboard.py`. If the *data* is stale, run `refresh` first.

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
STATUS.md        the watchdog's latest scoreboard
dashboard.html   generated — rebuild any time
dashboard.png    generated — the shareable image
```

`data/` is the only thing that exists nowhere else. Everything else is on
GitHub or regenerates itself.
