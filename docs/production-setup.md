# Separating production from development

## Current state — already done for you

| folder | role |
|---|---|
| `C:\Users\BromC\sports-machine` | **production**. Clean, holds the data, the scheduled task runs here. |
| `C:\Users\BromC\sports-machine-dev` | **development**. Cloned, dependencies installed, 104 tests passing. |

Nothing was moved and the scheduled task was not touched — it still points at
production and next runs at 11:30am. Verified after setup:

- dev has **no `data/`**, so it cannot reach the real database by accident
- dev's `audit.py` reports **60 passed, 20 skipped, 0 failed** — it degrades
  rather than crashing or falsely passing
- dev reads production data only when you opt in, and that worked
- production still shows 12,219 games and a clean working tree

**What is left for you:** start editing in the dev folder instead of production.
That is the only habit that has to change. Steps 1 and 2 below are kept for
when you rebuild, not because you need to run them now.

---

## The problem this solves

Right now the scheduled job runs `git pull` and then executes code **in the
same folder you and I are editing**. At 11:30am it places paper bets, writes to
the database and settles wagers using whatever happened to be saved at 11:29.

There is now a guard: `scheduled_check.bat` refuses to run if its folder has
uncommitted changes, and writes the refusal to `logs\cronstatus-latest.txt`.
That stops bad code running — but it means **every day you leave an edit
unsaved, the job does nothing at all.** Two folders fixes that properly.

---

## The plan I recommend

Your **current folder stays production.** It already has the data, the
scheduled task, and the backups. Nothing moves, nothing gets repointed, and the
irreplaceable thing — `data/` — is never touched.

You get a **new folder for editing.**

| Folder | Role |
|---|---|
| `C:\Users\BromC\sports-machine` | **production** — the scheduled task runs here. Don't edit it. |
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

You should see **100 passed**. That works with no data at all — which is the
point of those tests.

---

## Step 2 — what the dev copy can see *(already set up)*

The dev folder starts with **no `data/`**, so it cannot touch your real
database, and `audit.py` there will mostly report SKIP. That is correct and
safe.

When you want the dev copy to read the **real** data, set this for that
PowerShell window only:

```powershell
$env:SPORTS_MACHINE_DATA_DIR = "C:\Users\BromC\sports-machine\data"
```

It lasts until you close the window. **Do not** use `setx` for this — that
would make it permanent, and then a stray command in dev could write to
production's database.

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
`git pull --rebase --autostash` at the start of every run.

**Production only ever pulls.** Never edit, never commit, never push from
`C:\Users\BromC\sports-machine`. If you do, the guard will refuse to run the
job and say so in the log — which is the safe failure, but it means nothing
collected that day.

---

## If you would rather move the data instead

Some people prefer production and development to share one database in a
neutral place. It is more flexible and it is also the version where something
can go wrong, so only do this if you want it:

```powershell
# 1. Stop the scheduled task first.
Disable-ScheduledTask -TaskName "SportsMachine-CronStatus"
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
# 6. Turn the task back on.
Enable-ScheduledTask -TaskName "SportsMachine-CronStatus"
```

If step 5 shows the old path, close and reopen PowerShell — `setx` only affects
new windows.

---

## Step 4 — if you ever do repoint the scheduled task

Only needed if you decide production should live somewhere else:

```powershell
$repo = "C:\sports-machine-prod"
$a = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\cmd.exe" `
     -Argument "/c `"$repo\scheduled_check.bat`"" -WorkingDirectory $repo
Set-ScheduledTask -TaskName "SportsMachine-CronStatus" -Action $a
```

Then check it took:

```powershell
(Get-ScheduledTask -TaskName "SportsMachine-CronStatus").Actions.Arguments
```

---

## What to check afterwards

**1. The dev copy works and is harmless.**

```powershell
cd C:\Users\BromC\sports-machine-dev ; python paths.py ; python -m pytest tests/ -q
```
Expect: a `data` path **inside the dev folder**, and 100 tests passing.

**2. Production still points where you think.**

```powershell
cd C:\Users\BromC\sports-machine ; python paths.py ; python audit.py
```
Expect: your real data path, and **82 passed, 0 failed**.

**3. The scheduled job is not refusing.** After the next 11:30am or 10pm run:

```powershell
Get-Content C:\Users\BromC\sports-machine\logs\cronstatus-latest.txt -TotalCount 6
```
If it says **REFUSED TO RUN**, the production folder has uncommitted changes.
Fix with:

```powershell
cd C:\Users\BromC\sports-machine ; git status --short ; git checkout -- .
```

Only run that in **production**, and only when you are sure the changes there
are not wanted — it discards them.

**4. Alerts still reach you.**

```powershell
cd C:\Users\BromC\sports-machine ; python notify.py
```

---

## Things that stay exactly as they are

- The **cloud** collector is unaffected — it runs on GitHub, from `main`.
- **Backups** keep going to `OneDrive\sports-machine-backups`.
- `archive/` is in git and identical in both folders.
- The three gates, the model and every threshold are unchanged. Nothing here
  touches whether the system may bet.
