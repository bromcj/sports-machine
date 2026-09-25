# Phase A review, step 2: the handoff's claims, one by one

Every claim in [phase-a.md](phase-a.md) (the Phase A handoff, `87db193`) marked
**confirmed**, **wrong** or **couldn't check**, with the evidence. The evidence
comes from [phase-a-review-independent.md](phase-a-review-independent.md),
written before this file was opened, and from the extra checks noted here.
"Confirmed" means re-derived, not re-read.

## Part 1 — for the owner

### §0 The four missing briefs

| Claim | Verdict | Evidence |
|---|---|---|
| Production's four dated briefs were deleted through Explorer at 12:36 AM and are in the Recycle Bin, next to task files cleared from Downloads at 12:18 | **confirmed** | Recycle Bin (read only): `2026-09-22-code-task.md`, `2026-09-23-next-task.md`, `2026-09-24-next-task.md`, `2026-09-24-review-task.md`, all "deleted 9/25/2026 12:36 AM" from `…\sports-machine\docs\briefs`; eight Downloads files deleted 12:18–12:19 AM |
| The 11:30 AM run will refuse because of it | **confirmed** | It did: `logs\cronstatus-latest.txt`, "Generated Fri 09/25/2026 11:30:02 … REFUSED TO RUN: this folder has uncommitted changes … Nothing was pulled, predicted, bet, settled or scored" |
| Nothing is lost; `git checkout -- docs/briefs` restores them | **confirmed** | All four are tracked on `main` (`git ls-files docs/briefs`); the command restores tracked files and touches nothing else |
| The session did not delete them; it only read production | **couldn't check** directly (no audit log of that session); consistent with the Recycle Bin showing an Explorer delete and with every production command in the handoff being a read |

### §1 The short version

| Claim | Verdict | Evidence |
|---|---|---|
| Built, tested, on its own branch; production untouched; 0 API credits | **confirmed** | Production is on `d3dadc5` (main minus the archive commit). The paid key's balance in production's `api_usage` is 41,734 from 9/24 21:00 ET through the collector's 11:45 AM run today, so nothing spent it in between; the cloud's key logs show only the three scheduled pulls |
| "a polling loop that can never go past its credit limits" | **wrong** | A billed call whose ledger write fails is never counted, so the loop keeps spending: a simulated day with the database busy billed 375 credits against a ledger showing 6. Two loops reach 6,003 of 6,000 (check then record is not atomic). A dev checkout and production each get their own 6,000. All three reproduced by independent verifiers |
| "paper orders that can only fill against prices seen after them" | **confirmed** | Every look-ahead case failed to leak (a price at the order's own microsecond waits; out-of-order inserts; A-V4 re-derived: 0 fills at or before the order) |
| It cannot place a real order: no code can send one, the database refuses one, and the audit checks both | **partly wrong** | No code sends one (grep and AST: the only POST is the opt-in ntfy alert) — confirmed. The database's CHECK refuses every non-paper mode, even through a `str` subclass that fooled `submit` — confirmed. "The audit checks both" overstates it: its static scan missed 11 of 24 plausible order-sending shapes and skipped `tests/`, top-level names starting with `data`, and `.bat` files; with three order-capable modules wired into `scanner/paper.py` it still printed 100 passed |
| Pinnacle does not double a request's cost; lists longer than ten books do; the props collector could name ten books and pay half | **confirmed** | The Odds API docs: "every group of 10 bookmakers is the equivalent of 1 region"; the cloud's logs (Pinnacle plus three books, one market) show 1 credit per sport (475 → 474 → … → 470); the collector's 15 books × 2 markets logged cost 4 |
| Rain delays made the new fair-price code call pregame prices in-play; found against real data; fixed | **confirmed, but the fix went too far** | A-V1's first run found 10 disagreements on two rain-delayed games and the results log says so. The fix (keep the latest reported start) lets a start reported *after* a game began reopen it: the archive holds 34 in-play pulls accepted as pregame, e.g. Astros at Rockies 2024-04-27. Fixed in this review |
| "Paper orders were filling at in-play prices: 617 of 3,629 in a test on real prices. Now nothing fills after a game starts" | **mislabelled; the fix is confirmed** | Re-derived: 3,652 orders, 3,629 fills before the fix; 617 of the fills were in play (640 orders reached in-play prices: 617 filled plus 23 above their limit). So it is 617 of 3,629 *fills*. After the fix: 3,012 fills, 0 in play |

### §2 What was built

| Claim | Verdict | Evidence |
|---|---|---|
| A1: 7 new tables, nothing existing touched | **confirmed** | 27 of 27 pre-existing schema objects and all 12 tables byte-identical after `db.init()` on a copy of production; 0 ALTER/DROP/UPDATE/DELETE in an SQL trace; idempotent |
| A1: a real 32-game slate from 15 books → 732 markets, 1,464 prices, 0 rejected | **confirmed** | Re-run on the 2026-09-24 slate: 32 events, 732 markets, 1,464 prices, 0 rejects |
| A1 fees: every EV is after fees; the fee rule is on every price; 50¢ costs $0.0175, 100 cost $1.75 | **confirmed** | 415 hand-computed cases agree, including Kalshi's own worked example; A-V2 reproduces |
| A1 fair_value: Pinnacle → book average → venue mid, says which; agrees exactly on all 2,012 pregame prices of production's last week | **confirmed for that week; wrong as a general claim** | 2,012 / 0 / 354 reproduced with independent arithmetic. On the real week ending 2026-06-06 the audit's own A-V1 fails (1,794 agree, 4 disagree): a book that stopped quoting before a rain delay drags the cutoff back. The mid fallback had no in-play cutoff at all. Both fixed in this review |
| A2: cadence by time to start; limits checked before every call; 12 + 12 tests with a fake clock and API | **confirmed as stated; the limits leak** | 12 tests in each file at `87db193`; cadence matches the brief (2/5/15/60 min, none once started); but see "never go past its credit limits" above |
| A3: stake, days, EV per year, capital locked by strategy and month; shown in the scoreboard | **confirmed** | Hand-recomputed a two-level partial fill (all 8 fields equal); scoreboard figures matched a constructed ledger with 0 mismatches |
| A4: a strategy is one file; own block and three gates; `arm("name")`; 15 tests including one strategy's pass giving another nothing | **confirmed as stated; the gate had holes** | 15 tests, including `test_a_strategy_passes_gate_2_on_its_own_positions_only`. But gate 2's coverage counted graded-but-unsettled positions (a strategy armed at a true coverage of 17%), positions were graded before the start, and the 3-SE bar passes a no-skill favourite buyer 5–41% of the time on `realized_ev`. Fixed in this review |
| A5: fills only against the next price, never more than the size shown, a daily risk cap; A-V4 3,652 orders, 0 filled at a price from before the order | **partly wrong** | Next-price and A-V4 confirmed. "Never more than the size shown" holds per order but not across orders: five orders took 150 contracts from one 30-lot, and one maker took 80 from an unchanged 20-lot seen four times. The cap is checked before every order in ET days (confirmed), but per-fill fee rounding could take a stake past the exposure it counted. Fixed in this review |
| A6: 9 new test files, 14 new audit checks, 485 tests pass (347 before), audit 100 passed 0 failed in dev and on production's copy | **confirmed** | 9 files; main 86 checks → branch 100, the 14 being `scanner_checks()`; 485 passed with `data_golden/` (main: 347); 100/0/0 both places |
| Everything tested against real data was pre-registered in `docs/experiments.md` first, with S0 for future strategies | **confirmed** | S0 and A-V1–A-V4 committed at 00:48, before the first code (00:51); the A-V1 clarification came after `fair.py` existed but before A-V1's first run (01:00:46 vs 01:00:57). It narrowed the rule; the results log records the first run's failure |

### §3 The credit budget

| Claim | Verdict | Evidence |
|---|---|---|
| One call = 3 credits (three markets, ten books) | **confirmed** | docs and `call_cost()` |
| The brief's cadence would cost about 55,600 a month for NFL, NBA and NHL | **confirmed** | 55,607 re-derived independently |
| Brief: 6,000 over 42 polling days, projected ~4,410; normal: 12,000 a month, projected ~10,300 (+3,000 = ~13,300 of 15,000) | **wrong** | The arithmetic of `poll --plan` is internally right, but it assumes a fixed allowance each day. The real loop rolls unspent credits forward and re-picks its level every 5 minutes, so it spends the whole 6,000 and the whole 12,000 (simulated with the real `Poller`: 6,000 of 6,000; 12,000 of 12,000) — 15,000 of 15,000 with the collector, not ~13,300. The hard caps still hold |
| What it buys: every 20–30 min near a game (brief), 10–15 min (normal) | **wrong in the same way** | The real loop ran at levels 3–5 (15–30 min) during the brief and 0–3 (2–15 min) normally |
| Every game still gets a price in its last 30 minutes, tested | **confirmed, with a limit** | Every ladder level's closing tier is ≤ 30 min, and a property test covers random slates; on a day the governor pauses (brief spent) there are no closes |
| A daily pace so one busy day cannot spend everything | **wrong at the edges** | After 42 polling days the pace offers everything left in one day, and so does the month pace on a month's last day. The first is tightened in this review; the second is documented |
| `poll --plan` makes no calls | **confirmed** | Zero socket, process or file events under an audit hook |

### §4 Judgment calls

| Claim | Verdict | Evidence |
|---|---|---|
| 1. A branch, not production | **confirmed** | |
| 2. The schema change was made without asking because "the brief lists the tables" | **overstated** | The brief specifies `prices` and `markets` column by column and the paper-order fields; `credit_ledger`, `paper_fills`, `paper_positions` and `gate_looks` are the builder's design. The change is verified purely additive, so no data was at risk |
| 3. The cadence is a target the budget cannot meet | **confirmed** | |
| 4. Gate 2 measures only `info` or realized profit; a third measure would make gates easier, so the owner decides | **confirmed as a choice; the realized-profit bar was not calibrated** | See §2/A4. `realized_ev` now fails closed until a bar calibrated for its payoff shape is pre-registered |
| 5. Stricter than the brief in three places (same fair source at both ends; no fill after the start; two looks a day) | **confirmed** | All three are in the code and tested |
| 6. The integration matrix is `tests/test_entry_points.py`, which found the new commands by itself | **confirmed, with one false alarm** | It flags an unreachable scanner module and an undocumented mode; it wrongly reports a strategy file as unreachable although `strategies.load()` imports it |
| 7. A `strategies` command the brief did not name | **confirmed** | |
| 8. The 25% alert is a "worth a look" line; 10% pops up | **confirmed** | 24% → WARNING (ALERTS.md), 9% → CRITICAL (toast). Both fire strictly *below* the threshold |

### §5 What is not done

All **confirmed** as stated: the loop has never made a real call; `_spawn()` has
never run and whether it outlives the Task Scheduler job is unverified (the task
has a 15-minute limit — still unverified); Kalshi's fee schedule PDF returned
HTTP 429 (to this review too); the adapters have only seen documented payloads;
no strategy exists; the four open items from the 9/24 review are unchanged.
One addition: Kalshi's *terms* were not read before the adapter was written,
which the brief's rule asks for.

### §6 Numbers

| Claim | Verdict | Evidence |
|---|---|---|
| pytest 485 passed (347 at the start) | **confirmed** | 485 in dev; main 347 with `data_golden/` |
| golden OK | **confirmed** | also on production's data, against main's own baseline |
| audit 100/0/0 in dev and on production's copy | **confirmed** | |
| CI (Python 3.11) success | **confirmed** | 483 passed, 2 skipped on 3.11 |
| 0 credits | **confirmed** | see §1 |
| production on `d3dadc5` | **confirmed** | |
| backup `machine-2026-09-25-0045.db`, verified, all 12 tables | **confirmed** | opened read-only: integrity ok, 12 tables |
| scheduled tasks read at 1:37 AM: CronStatus last 9/24 10 PM result 0, next 11:30; Collect last 9 PM, next 11:45 | **confirmed** | same values from `Get-ScheduledTask` (read only) |

## Part 2 — for the reviewer

| Claim | Verdict | Evidence |
|---|---|---|
| A maker-loop mutation spun forever before a guard was added; check no other way for time to stand still | **confirmed** | the guard `quotes[0]["captured_at"] <= after → break` is there; mutation runs of `next_quotes_after` terminate |
| The in-play cutoff uses the LATEST reported start; "hindsight about whether a price was in play, not information about the outcome" | **wrong** | A later start reported after a game began reopens it: in-play prices become fair values, fills and fair closes (Astros at Rockies). And `fair_value(at)` then depends on data captured after `at`. Fixed: a start may move later only if reported before the new start |
| `line IS ?` compares floats; the consensus needs both sides from one book at one pull | **confirmed** | books at a different line are never mixed; lines round-trip exactly |
| The loop's risks listed in §8.3 (two loops, failed calls, charging events, ET days, the stop-file race) | **confirmed as the right places to look** | Two loops overspend (6,003); a failed *ledger write* is the leak the list missed; a charging events endpoint is ledgered from the header (not a defect: the endpoint is free by docs and data); ET days are correct; the stop-file race leaves polling off until the next run |
| `record_backtest()` added, `explain()` byte-identical for sports | **confirmed** | identical for mlb, nfl, nba; golden OK. `record_backtest` itself had no guard against a sport's name (fixed) |
| `audit.py`'s scans find senders by call name; a socket, an aliased import could slip past | **confirmed — they do** | 11 of 24 shapes slip past (see §1) |
| Things got wrong first and fixed (cost claim, float trap in 386 of 594 cases, `venues.sportsbook()` shadowing, `family('kalshi:x')`, A-V1 rain delay, A-V4 in-play fills, pace-vs-cap by word, gate-1 prefix) | **confirmed where checkable** | the cost claim, A-V1, A-V4, the pace field (`OverBudget.limit`) and the whole-heading rule are in the code and history; the 386 of 594 count is from the session and could not be re-derived |
| §9: `scheduled_check.bat` bytes up to the line after `git pull` unchanged (offset 3,062) | **confirmed** | offset 3,062 before and after |
| §9: last code commit `c6b88b5`; pytest 485 at `bf77913`; `c6b88b5`'s test file passed (15) | **confirmed** | 15 tests in the file; 485 on `87db193`, which adds only the report |
| §10: the commands exist and behave as described | **confirmed** | `poll --plan/--status/--ensure` and `strategies` run as described on a production copy; `poll --live` was not run (rules) |
| §11: 26 files added (plus the report), 19 modified, none deleted | **confirmed** | `git diff --diff-filter=A` lists 27 including the report; 19 modified; 0 deleted |
| `scheduled_check.bat`: "one line, below `git pull`" | **roughly** | one command line (`poll --ensure`) plus three comment lines and an `echo.`, all below the pull |

## Tally

56 claims in the tables above, plus §5's list. **44 confirmed** (several with
a caveat, noted in the row), **11 wrong or partly wrong** (the credit limits
"never" exceeded; the audit "checks both"; the rain-delay fix going too far;
A-V4's "orders"; fair_value agreeing "exactly" beyond the one week tested; the
fill-size rule across orders; the budget projections and what they buy (two
rows); the daily pace at its edges; the schema-authority reason; the
latest-start rule), **1 couldn't check** (who deleted the briefs). Inside
confirmed rows, three sub-points could not be re-derived: the float-trap count
(386 of 594), whether the loop outlives the Task Scheduler job, and Kalshi's fee
PDF.
