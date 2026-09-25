# Report point A — the scanner's foundations (handoff)

Brief: [docs/briefs/2026-09-25-next-task.md](../briefs/2026-09-25-next-task.md), Phase A only.
Written 2026-09-25, ~2am ET. Three parts: **Part 1** is for the owner. **Part 2**
is for the fresh session that reviews Phase A, which should treat every sentence
here as a claim to test. **Part 3** is the exact prompt to start that session.

Every number below was quoted from a command run in this session. Where
something was not run it says **not run**; where I believe something but did
not check it, it says **unverified**.

Scope: branch **`phase-a-foundations`**, 24 commits (`git log main..phase-a-foundations`,
the last being this report), plus one docs-only commit on `main` (`22473e6`, the
brief archived by date).

---
---

# PART 1 — for the owner

## 0. Before 11:30 this morning: one thing only you should decide

**Four files are missing from the production folder, and the 11:30am run will
refuse to start because of it.** Production's `docs/briefs/` is empty. The four
dated briefs in it were deleted through File Explorer at **12:36 AM** — they are
in your Recycle Bin, next to the task files cleared out of Downloads at 12:18.
This session did not delete them (every command it ran in production only
read). The scheduled job refuses to run while any tracked file is missing, so no
paper bets would be placed or settled until it is fixed.

Nothing is lost: all four are in git. To put them back:

```powershell
cd C:\Users\BromC\sports-machine ; git checkout -- docs/briefs
```

If you meant to remove them from the project, that is a commit made from the dev
folder instead; say so in the next session. I did not restore them myself
because it was your deliberate action.

## 1. The short version

**Phase A is built, tested and waiting for its review, and none of it is running
in production.** It is on its own branch; production still runs the old code
and is untouched. Nothing was spent: **0 API credits**.

What exists now: one price table for every venue (sportsbooks, Kalshi,
Polymarket), one function that says what an outcome is fair value, the fees for
each venue, a polling loop that can never go past its credit limits, paper orders
that can only fill against prices seen *after* them, capital accounting, and a
system where each future strategy gets its own three gates. It is **switched
off**, it has **no strategies**, and it **cannot place a real order** — there is
no code that can send one, the database refuses one, and the audit checks both.

Three findings you should know about, each fixed:

- **The cost rule this project believed was wrong.** Pinnacle does not double
  what a request costs; lists longer than ten books do. Proven from the cloud's
  own logs. It means the props collector could name ten books and pay half
  (not changed — your call).
- **Rain delays** made the new fair-price code think real pregame prices were
  in-play ones. Found by checking it against real data; fixed.
- **Paper orders were filling at in-play prices**: 617 of 3,629 in a test on
  real prices. Now nothing fills after a game starts.

## 2. What was built, and the evidence

| brief | what it is | how I know it works |
|---|---|---|
| A1 venues, one price table | 7 new tables (only new ones — nothing existing touched); an adapter each for sportsbooks, Kalshi, Polymarket | proved on a copy of production's database: the 12 existing tables' definitions and row counts identical after; a real 32-game slate from 15 books stored as 732 markets and 1,464 prices, 0 rejected |
| A1 fee models | every EV is after fees; the fee rule is written on every price | Kalshi: 50¢ costs $0.0175 a contract, 100 cost $1.75 (A-V2) |
| A1 fair_value | Pinnacle → average of the books → the venue's own mid; says which | agrees exactly with the existing fair-price code on all **2,012** pregame prices of production's last week (A-V1) |
| A2 polling | a loop, cadence by time to the start, limits checked before every call | see §3; 12 + 12 tests with a fake clock and a fake API |
| A3 capital | stake, days until the money comes back, EV per year, capital locked by strategy and month | tests; shown in `python run_daily.py scoreboard` |
| A4 strategies and gates | a strategy is one file; each gets its own `validation.json` block and its own three gates; `arm("name")` | 15 tests, incl. one strategy's pass giving another nothing |
| A5 paper execution | orders filled only against the next price seen, never more than the size shown, a daily risk cap per strategy | A-V4: 3,652 orders on real prices, **0** filled at a price from before the order |
| A6 tests, audit, docs | 9 new test files; 14 new audit checks; docs | **485 tests pass** (347 before); **audit 100 passed, 0 failed** in dev and on a copy of production's data |

Everything that tests code against real data was written down in advance, with
its pass rule, in `docs/experiments.md` (A-V1 to A-V4) — and, as the brief
requires, the rules every future strategy inherits (S0).

## 3. The credit budget — the math the brief asked for

One call returns a whole sport's games for **3 credits** (three markets, ten
books). The brief's cadence — every 2 minutes near a start, then 5, 15, 60 —
would cost **about 55,600 credits a month** for NFL, NBA and NHL together. That
cannot fit 6,000 for the whole brief or ~15,000 a month normally.

So the loop slows down in fixed steps when the budget is short, far-off games
first:

| | allowed | projected | what that buys |
|---|---|---|---|
| this brief | 6,000 over 42 polling days | ~4,410 | prices every 20–30 min in the last 90 min before each game |
| normal, on the 20K plan | 12,000 a month (+ the props collector's 3,000) | ~10,300 (+3,000 = ~13,300 of 15,000) | every 10–15 min near each game |

Whatever step it is on, **every game still gets a price in its last 30
minutes** — tested, not assumed — so every game can be graded. Three limits are
checked before each call: the brief's 6,000, 12,000 a month, and a daily pace so
one busy day cannot spend everything. `python run_daily.py poll --plan` prints
all of this; it makes no calls.

## 4. Judgment calls — where I deviated or decided

1. **A branch, not production.** Production pulls `main` at every run. Phase A
   stays on `phase-a-foundations` until the review says "safe to build on".
2. **The database change was made without stopping to ask**, because the brief
   lists the tables and says not to stop — but kept to new tables only, proven
   harmless on a copy of production's data.
3. **The brief's polling cadence is treated as a target** the budget cannot
   meet (§3).
4. **Gate 2 measures only what the brief names — `info` or realized profit.**
   For the two price-taking strategies planned (E1 sharp-line, E2 Kalshi
   taker), neither is ideal: `info` measures forecasting, which they do not do,
   and realized profit is honest but needs many bets. A third measure would make
   those gates easier to pass, so that is **your decision** when E is
   pre-registered.
5. **Stricter than the brief in three places:** a strategy's `info` needs the
   same fair source at both ends (the flaw the last review found in the MLB gate
   is designed out); no fill after a game starts; gate 2 re-tested at most twice
   a day per strategy.
6. **The brief's `python -m sports_machine scoreboard`** is `python run_daily.py
   scoreboard` (there is no such package). **"The integration matrix script"** is
   now `tests/test_entry_points.py`, which found the new commands by itself.
7. **Added a command the brief did not name,** `python run_daily.py strategies`,
   because the gate code needed a real caller.
8. **Alerts at 25% and 10% credits left** use the monitor's existing levels: the
   25% one is a "worth a look" line in `ALERTS.md`, not a desktop pop-up; 10% pops
   up.

## 5. What is not done, or not proven

- **The loop has never made a real call** (on purpose — Phase C4 turns it on).
  Its HTTP path is tested against a fake API only.
- **Starting the loop in the background has never actually been done**, and
  whether a process started by Task Scheduler outlives the task is
  **unverified**. And the scheduled job only checks on it twice a day, so a
  crash at 1pm could go unnoticed until 10pm. C4 should decide whether the loop
  needs its own scheduled task.
- **Kalshi's fee schedule itself was not read** — the website refused three
  times (HTTP 429). The fee numbers come from the brief, Kalshi's API docs and
  other sources; one fee type (`flat`) is unread and the code refuses it.
- **Kalshi and Polymarket adapters have only seen payloads copied from their
  docs**, never a real response. The monitor check that Kalshi still allows
  sports for this account, and the Kalshi demo environment, are Phase C.
- **No strategy exists**; the strategy machinery was exercised with test
  strategies only.
- The four open items from the 9/24 review that are yours (gate 2's mixed
  fair source for MLB, 14-hour-old prices, the unmaintained closing table,
  the paid data in the public repo) are **unchanged**.

## 6. Numbers, quoted

```
pytest (dev, with the golden data)    485 passed        (347 at the start)
golden test                           GOLDEN OK - nothing moved
audit.py, dev's data                  100 passed, 0 failed, 0 skipped
audit.py, copy of production's data   100 passed, 0 failed, 0 skipped
CI (Python 3.11) on the branch        success
API credits spent this session        0
production                            untouched; on d3dadc5; see §0
backup before the first change        C:\Users\BromC\sports-machine-dev-backups\
                                      machine-2026-09-25-0045.db, verified, all 12 tables
```

Production's scheduled tasks, read at 1:37 AM: `SportsMachine-CronStatus` last
ran 9/24 10:00 PM, result 0, next 11:30 AM; `SportsMachine-Collect` last ran
9:00 PM, result 0, next 11:45 AM.

## 7. What happens next

1. Restore the four briefs (§0), or tell the next session they should go.
2. Start the review with the prompt in **Part 3** — a new session, so it did not
   write this code. If it says "safe to build on", it merges this branch; the
   next scheduled run then picks it up, and it stays switched off.
3. Only then Phase B.

---
---

# PART 2 — for the reviewing session

## 8. Where I would look first

**1. `scanner/paper.py` `simulate()` and `_simulate_maker()`.** The most intricate
code, and the rules are the product: next observation only, levels and sizes,
trade-through for makers, `partial_open`, expiry, and the start cutoff I added
after A-V4. A mutation (allowing a fill at the order's own moment) made the maker
loop spin forever before I added a guard; check there is no other way for time to
stand still. Book orders: size is dollars, filled at the *observed* price
(contracts = stake ÷ price) — check the limit semantics for books.

**2. `scanner/fair.py` — the in-play cutoff uses the LATEST reported start.**
`markets.event_start` is overwritten by every later sighting, so a query about an
earlier moment uses a start learned afterwards. I argued that is hindsight about
whether a price was in play, not information about the outcome. Decide whether
you agree. Also: `line IS ?` compares floats; the consensus needs both sides from
the same book at the same pull.

**3. `scanner/budget.py` + `poll.py` — can the loop spend more than 6,000?**
Two loops at once (the heartbeat test is age-based); a call failing without
headers (counted at its estimate); the events endpoint starting to charge (it
would be ledgered, not pre-checked); ET day boundaries; `days_polled` counting
only days with a metered call; the governor's 1-minute projection versus 30-second
ticks. `ensure()`'s stop-file handshake has a race if the old loop hangs.

**4. `model/validation.py`** — I added `record_backtest()` and changed `explain()`
to say "backtest" for a strategy. The sport wording should be byte-identical;
the golden test said nothing moved.

**5. `audit.py`'s new scans** find senders by call *name*
(post/put/patch/delete/request/urlopen/send). A socket, `http.client` via another
name, or an aliased import could slip past. I planted one bad module and it was
caught; try to write one that is not.

**Things I got wrong first and fixed:** the Pinnacle cost claim (inherited); a
test comment claiming a float trap at 50¢ (it bites elsewhere — 386 of 594
cases); `venues.sportsbook()` shadowing its own module; `family('kalshi:x')`
accepted; A-V1's rain-delay conversion bug; A-V4's in-play fills; the pace-vs-cap
decision made by matching the word "pace"; and gate 1 accepting a heading
*prefix*, which would have let "E1" match an unrelated 2026-09-23 experiment.

## 9. What I did not verify

- The loop against the real API, and `poll --live` at all. **Not run** (credits).
- `_spawn()` — the detached start — **never executed**; Task Scheduler's job
  object and `CREATE_BREAKAWAY_FROM_JOB` are **unverified**.
- `scheduled_check.bat` was **not run**; I checked only that its bytes up to the
  line after `git pull` are unchanged (offset 3,062 before and after).
- Kalshi's fee-schedule PDF — **not read** (HTTP 429).
- Real Kalshi/Polymarket payloads — **never seen**.
- The last code commit is `c6b88b5`. On it: golden OK; audit 100/0/0 in dev and
  on the production copy; pytest 485 passed at `bf77913`, and the one test file
  `c6b88b5` changed passed again (15).

## 10. How to run it

```bash
cd C:/Users/BromC/sports-machine-dev
git fetch && git switch phase-a-foundations && git pull
python -m pytest tests/ -q                   # 485 with data_golden/ present
python tests/golden/capture.py               # GOLDEN OK - nothing moved
python audit.py                              # 100 passed, 0 failed, 0 skipped

# A copy of production's data - never the live folder:
python -c "import sqlite3; s=sqlite3.connect('file:C:/Users/BromC/sports-machine/data/machine.db?mode=ro', uri=True); d=sqlite3.connect('C:/some/copy/machine.db'); s.backup(d)"
# then copy data/statcast, models, nfl and *.parquet beside it, and:
SPORTS_MACHINE_DATA_DIR=C:/some/copy python audit.py

python run_daily.py poll --plan              # the budget arithmetic, free
python run_daily.py poll --status            # free
python run_daily.py poll --ensure            # 'switched off'
python run_daily.py poll --live              # REFUSED (exit 2) - never run it switched on
python run_daily.py strategies               # none registered
```

## 11. Files

**Added (26):** `scanner/` (`__init__`, `budget`, `capital`, `fair`, `fees`,
`gates`, `paper`, `poll`, `scoreboard`, `store`, `strategies/__init__`,
`venues/__init__`, `venues/kalshi`, `venues/polymarket`, `venues/sportsbook`);
`tests/` (`test_fair_value`, `test_fees`, `test_paper_fills`, `test_poll_budget`,
`test_poll_loop`, `test_scanner_schema`, `test_scanner_venues`,
`test_scoreboard`, `test_strategy_gates`); `docs/scanner.md`, `docs/venues.md`;
plus this report.

**Modified (19):** `CLAUDE.md`, `COMMANDS.md`, `README.md`, `audit.py`,
`backup.py`, `config.py`, `db.py`, `docs/decisions.md`, `docs/experiments.md`,
`docs/gates.md`, `docs/production-setup.md`, `docs/state-of-the-machine.md`,
`ingest/odds.py` (one function extracted, output proven identical),
`model/validation.py`, `monitor.py`, `props/collect.py` (comments only),
`run_daily.py`, `scheduled_check.bat` (one line, below `git pull`),
`tests/test_monitor.py`.

**Deleted:** nothing.

---
---

# PART 3 — the prompt for the fresh-session review

Start a **new** Claude Code session in `C:\Users\BromC\sports-machine-dev` and
paste this:

```text
You are reviewing Phase A of the scanner brief in this repo. You did not write
any of it. Treat every sentence of its handoff as a claim to test.

Read CLAUDE.md first; its rules apply. Then:
  git fetch && git switch phase-a-foundations && git pull

WHAT YOU ARE REVIEWING. The 24 commits in `git log main..phase-a-foundations`
(plus 22473e6 on main, which only archived the brief). The brief is
docs/briefs/2026-09-25-next-task.md - Phase A (A1-A6) and the rules at its top.
Nothing else: do not start Phase B, and add no features.

METHOD. The method of docs/briefs/2026-09-24-review-task.md (the old
REVIEW_TASK.md), scoped to these commits:
1. Form your own view first. Before opening docs/reports/phase-a.md, find the
   code for each of A1-A6 and judge whether it does what the brief asks. Run
   pytest, the golden test and `python audit.py`, in dev and against a COPY of
   production's data (SPORTS_MACHINE_DATA_DIR; copy the database with the
   sqlite backup API from a read-only connection). Write this up as
   docs/reports/phase-a-review-independent.md.
2. Then read docs/reports/phase-a.md and mark each claim confirmed, wrong, or
   couldn't check, with the evidence.
3. Fix what is wrong - one commit each, evidence in the message - and
   re-verify. Anything that reproduces badly is a defect, whichever document
   says otherwise.

AT MINIMUM, TRY TO BREAK THESE:
- No real order: write a module that could send an order and see whether
  audit.py catches it; try paper.submit with any mode but paper/placebo; try
  the database's CHECK directly.
- No look-ahead: construct a case where fair_value or paper.simulate uses a
  price captured after the moment asked about, or a fill at the order's own
  moment. Judge the in-play cutoff that uses the LATEST reported start time.
- No in-play fills, sizes never exceeded, a maker never filled by a mere touch.
- The credit limits: can the loop spend past 6,000, the month's 12,000 or the
  daily pace - two loops at once, failed calls, a charging events endpoint, ET
  midnight? Re-derive `python run_daily.py poll --plan`'s numbers yourself.
- The gates: a strategy's block is guard-compatible (only_paper_changed), no
  strategy inherits another's pass, arm() refuses without gates 1 and 2, gate 2
  is looked at at most twice an ET day, and the sequential-testing numbers in
  docs/gates.md reproduce.
- fair_value vs bets/log.fair_prob on a copy of production's data (A-V1), and
  the pre-registration order in git (docs/experiments.md before the code).
- The schema change is additive, backup.py verifies all 19 tables, and a
  restored backup still passes the golden test.
- Every doc claim with a number in it.

RULES. No API credits: never run `poll --live`, never set POLLING_ENABLED to
True, never run morning/close/props collection. Work on copies of data/; touch
production (C:\Users\BromC\sports-machine) only to read it. Stop conditions as in
the brief; if you stop, write docs/reports/BLOCKED.md.

VERDICT. Write docs/reports/phase-a-review.md, in plain English for a
non-programmer: (1) "safe to build on" or "not safe to build on"; (2) what was
wrong and is now fixed; (3) what is wrong and not fixed, and what it would take;
(4) what was claimed and confirmed.

ONLY IF THE VERDICT IS "safe to build on": merge the branch into main
(`git switch main && git pull && git merge --no-ff phase-a-foundations`), run
pytest, the golden test and audit.py again, push, and after production's next
scheduled run confirm from logs\cronstatus-latest.txt that it pulled the merge
and did not refuse, and that `python run_daily.py poll --ensure` there prints
that polling is switched off. If the verdict is "not safe", do not merge.
```
