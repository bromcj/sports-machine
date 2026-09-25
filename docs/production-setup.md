# Separating production from development

## Current state (checked 2026-09-24)

| folder | role |
|---|---|
| `C:\Users\BromC\sports-machine` | **production**. Holds the real data; both scheduled tasks run here. It only ever pulls. |
| `C:\Users\BromC\sports-machine-dev` | **development**. Edit here, run Claude Code here, push from here. |

- dev **has its own `data/`** — a separate database, not production's. The
  props collector's first, hand-fired run wrote there, so that collection
  lives in dev, not production. dev also holds `data_golden/` (the fixed copy
  the golden test runs against) and `data_phase1/` (a copy the command sweep
  ran against). A command run in dev reads and writes dev's `data/` unless you
  point it elsewhere.
- `python -m pytest tests/ -q` passes: 690 passed, 2 skipped on a checkout
  without `data_golden/` (the two skipped are the golden test), and 692
  passed in the dev folder, which has it (both measured 2026-09-25 on the
  `phase-a-foundations` branch at 201a80e).
- `python audit.py` reports **100 passed, 0 failed** against a copy of
  production's data.

**What is left for you:** edit in the dev folder, never in production. That
is the only habit that has to change. Steps 1 and 2 below are kept for when you
rebuild, not because you need to run them now.

---

## The problem this solves

The scheduled job runs `git pull` and then executes code **in the folder it
lives in**. At 11:30am it places paper bets, writes to the database and settles
wagers using whatever is in that folder at the time.

So `scheduled_check.bat` guards itself. First it throws away the two changes
the machine makes to tracked files on its own: `STATUS.md`, and
`validation.json` when only its gate-2 block changed (the `paper` step
recomputes that from the database minutes later). Anything else uncommitted —
a real edit — makes it **refuse, loudly**: `logs\cronstatus-latest.txt` says
REFUSED TO RUN and lists the files, `ALERTS.md` gets an ERROR, a desktop alert
pops up, and the task exits with code 1 so Task Scheduler shows it failed.
Nothing is pulled, bet or settled that run.

That stops bad code running — but it means **every run you leave an edit in
production, the job does nothing at all.** Two folders fixes that properly.

(It also runs from a copy of itself in `%TEMP%`, because its own `git pull` can
rewrite the file while Windows is still reading it.)

---

## The plan

Your **current folder stays production.** It already has the data, the
scheduled tasks, and the backups. Nothing moves, nothing gets repointed, and
the irreplaceable thing — `data/` — is never touched.

You get a **separate folder for editing.**

| Folder | Role |
|---|---|
| `C:\Users\BromC\sports-machine` | **production** — the scheduled tasks run here. Don't edit it. |
| `C:\Users\BromC\sports-machine-dev` | **development** — edit here, run Claude Code here |

Moving data and repointing a scheduled task are the two operations that can
actually lose something. This plan does neither.

---

## Step 1 — make the development copy *(already done)*

Kept for reference, or if you ever need to rebuild it:

```powershell
cd C:\Users\BromC
git clone https://github.com/bromcj/sports-machine.git sports-machine-dev
```

```powershell
cd C:\Users\BromC\sports-machine-dev
python -m pip install -r requirements-dev.txt
```

```powershell
python -m pytest tests/ -q
```

You should see everything pass (690 passed, 2 skipped on 2026-09-25 — the two
skipped are the golden test, which needs `data_golden/`). That works with no
data at all — which is the point of those tests.

---

## Step 2 — what the dev copy can see

A **fresh** clone has no `data/`, so it cannot touch your real database, and
`audit.py` there will mostly report SKIP. That is correct and safe. (Today's
dev folder is not fresh — it has its own `data/`; see the top of this page.)

When you want the dev copy to use the **real** data, set this for that
PowerShell window only:

```powershell
$env:SPORTS_MACHINE_DATA_DIR = "C:\Users\BromC\sports-machine\data"
```

Everything you run in that window then **reads and writes** production's
database. It lasts until you close the window. **Do not** use `setx` for this —
that would make it permanent, and then a stray command in dev could write to
production's database.

The variable moves `data/` and nothing else. `validation.json`, `STATUS.md`,
`archive/`, `logs/` and `ALERTS.md` stay the dev folder's own, and
`backup.py` still writes into the real backup folder — set
`SPORTS_MACHINE_BACKUP_DIR` as well if you do not want that.

To check which data a folder is pointed at, from either one:

```powershell
python paths.py
```

It prints the directory and whether it came from the variable or the default.

---

## Step 3 — how a change reaches production

```powershell
cd C:\Users\BromC\sports-machine-dev
# ... make your change ...
python -m pytest tests/ -q
python audit.py
git add -A
git commit -m "what changed and why"
git push
```

Production picks it up on its own: `scheduled_check.bat` runs
`git pull --rebase --autostash` at the start of every run that passes its
guard.

**Production only ever pulls.** Never edit, never commit, never push from
`C:\Users\BromC\sports-machine`. If you do, the guard will refuse to run the
job, loudly (log, `ALERTS.md`, desktop alert, failed task) — the safe failure,
but it means nothing collected that run. Running `refresh` in production
counts: it rewrites gate 1 in `validation.json`, which the guard will not throw
away (COMMANDS.md, *Weekly-ish*).

---

## If you would rather move the data instead

Some people prefer production and development to share one database in a
neutral place. It is more flexible and it is also the version where something
can go wrong, so only do this if you want it:

```powershell
# 1. Stop BOTH scheduled tasks first. The collector writes to data\ too.
Disable-ScheduledTask -TaskName "SportsMachine-CronStatus"
Disable-ScheduledTask -TaskName "SportsMachine-Collect"
```

```powershell
# 2. Back up before moving anything.
cd C:\Users\BromC\sports-machine
python backup.py
```

```powershell
# 3. Move it.
Move-Item C:\Users\BromC\sports-machine\data C:\sports-machine-data
```

```powershell
# 4. Tell BOTH folders where it went. This one is permanent, on purpose.
setx SPORTS_MACHINE_DATA_DIR "C:\sports-machine-data"
```

```powershell
# 5. New window, then confirm both agree.
cd C:\Users\BromC\sports-machine ; python paths.py
cd C:\Users\BromC\sports-machine-dev ; python paths.py
```

```powershell
# 6. Turn both tasks back on.
Enable-ScheduledTask -TaskName "SportsMachine-CronStatus"
Enable-ScheduledTask -TaskName "SportsMachine-Collect"
```

If step 5 shows the old path, close and reopen PowerShell — `setx` only affects
new windows.

Only `data/` moves. `validation.json`, `STATUS.md`, `archive/`, `logs/` and
`ALERTS.md` stay in each folder, and backups keep going wherever they went
before.

---

## Step 4 — if you ever do repoint the scheduled tasks

Only needed if you decide production should live somewhere else:

```powershell
$repo = "C:\sports-machine-prod"
$a = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\cmd.exe" `
     -Argument "/c `"$repo\scheduled_check.bat`"" -WorkingDirectory $repo
Set-ScheduledTask -TaskName "SportsMachine-CronStatus" -Action $a
```

`SportsMachine-Collect` runs `collect_props.bat` from the same folder; repoint
it the same way, with that file name and task name.

Then check they took:

```powershell
(Get-ScheduledTask -TaskName "SportsMachine-CronStatus").Actions.Arguments
(Get-ScheduledTask -TaskName "SportsMachine-Collect").Actions.Arguments
```

---

## What to check afterwards

**1. The dev copy works and is harmless.**

```powershell
cd C:\Users\BromC\sports-machine-dev ; python paths.py ; python -m pytest tests/ -q
```
Expect: a `data` path **inside the dev folder**, and every test passing.

**2. Production still points where you think.**

```powershell
cd C:\Users\BromC\sports-machine ; python paths.py ; python audit.py
```
Expect: your real data path, and **100 passed, 0 failed**.

**3. The scheduled job is not refusing.** After the next 11:30am or 10pm run:

```powershell
Get-Content C:\Users\BromC\sports-machine\logs\cronstatus-latest.txt -TotalCount 6
```
If it says **REFUSED TO RUN** (so will `ALERTS.md`), the production folder has
an uncommitted change the job would not throw away. Fix with:

```powershell
cd C:\Users\BromC\sports-machine ; git status --short ; git checkout -- .
```

Only run that in **production**, and only when you are sure the changes there
are not wanted — it discards them.

**4. Alerts still reach you.**

```powershell
cd C:\Users\BromC\sports-machine ; python notify.py
```

It says which channels delivered. It leaves a test alert in `ALERTS.md` until
the next scheduled run rewrites it.

---

## Things that stay exactly as they are

- The **cloud** collector is unaffected — it runs on GitHub, from `main`.
- **Backups** keep going to `OneDrive\sports-machine-backups`.
- `archive/` is in git and identical in both folders.
- The three gates, the model and every threshold are unchanged. Nothing here
  touches whether the system may bet.
