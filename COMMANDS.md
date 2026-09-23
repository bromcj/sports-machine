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
| **`machine_daily.bat`** | **Double-click.** Pulls what the cloud collected into your local database. |

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

## Occasional

```
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
python features/build_training.py   rebuild the training table + retrain + save
```

---

## What costs API credits

The Odds API free tier is **500 credits a month**, and one pull costs
**one credit per in-season sport**.

**Spends credits:** `morning`, `close`, `refresh`, and every cloud cron run.

**Free:** `picks`, `audit`, `dashboard`, `grade`, `validation`, `healthcheck`,
`machine_daily.bat`, and anything reading the database.

The three daily cron runs cost about 360 credits in October, when all four
sports overlap. That is the tightest month. **Do not add a fourth daily pull
without redoing that arithmetic** — the maths is written into
`.github/workflows/daily.yml`.

---

## The cloud schedule

Runs on GitHub's servers three times a day. Your computer can be off.

| Your time (Eastern) | Mode |
|---|---|
| 10:00 am | opening odds |
| 6:40 pm | closing odds — ~25 min before the 7:05 slate |
| 9:20 pm | closing odds — ~20 min before west-coast games |

Times are set in UTC, so in winter each fires an hour earlier on your clock.

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
