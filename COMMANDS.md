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
| `python audit.py` | 21 checks. All green = everything is sound. |
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

The raw version, if you want it:

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
```

---

## What costs API credits

The Odds API free tier is **500 credits a month**, and one pull costs
**one credit per in-season sport**.

**Spends credits:** `morning`, `close`, `refresh`, and every cloud cron run.

**Free:** `picks`, `audit`, `dashboard`, `grade`, `validation`, `healthcheck`,
`backup`, `cronstatus`, `machine_daily.bat`, and anything reading the database.

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
