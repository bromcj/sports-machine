# Phase A review, step 1: my own picture, before reading the handoff

Written 2026-09-25 between 02:40 and 13:00 ET, **before** opening
`docs/reports/phase-a.md` (two usage-limit pauses in between).
It is the ground truth steps 2 and 3 are compared against. Scope: the 24
commits in `git log main..phase-a-foundations` (00:46–01:44 ET on 2026-09-25,
ending at `87db193`) plus `22473e6` on main, judged against Phase A (A1–A6)
of `docs/briefs/2026-09-25-next-task.md` and the rules at its top.

## How this was done

- **Nothing was run in production, and no credits were spent.** Production
  (`C:\Users\BromC\sports-machine`) was only read. Its database was copied with
  sqlite's backup API from a read-only (`mode=ro`) connection into the session
  scratchpad (integrity `ok`, 12 tables: games 19,596, odds_snapshots 282,129,
  bets 34, market_close 7,194 …). A second, untouched copy was kept.
- **Every Python run went through a dead proxy** (`HTTPS_PROXY=http://127.0.0.1:9`
  and friends). Unsetting `ODDS_API_KEY` is not enough here, because
  `props/collect.py` — and now `scanner/poll.py` through it — reads the paid key
  from the registry. With the proxy set, `requests` and `urllib` both fail with a
  proxy error, and `audit.py` still passes 100/100, so no audit check needs the
  network. `POLLING_ENABLED` was never set to True, in a file or in memory.
- Ten reviewers each took one area (A1 schema and venues; A1 fees and
  `fair_value`; A2 credits; A2 the loop and production; A3/A5 paper and capital;
  A4 gates; A6 audit and tests; regression on production's data; every number
  in the docs; git history and scope). Each worked in its own throwaway git
  worktree and was told not to open the Phase A handoff. Every defect a reviewer
  reported was then handed to one or two separate verifiers — one told to
  reproduce it from scratch, and for serious ones a second told to refute it.
  Below, **confirmed** means a verifier reproduced it independently.

## Baseline, before any change

| Run | Dev data | Copy of production's data |
|---|---|---|
| `python -m pytest tests/ -q` | **485 passed** (69 s) | — (the suite touches no database) |
| `python tests/golden/capture.py` | **GOLDEN OK - nothing moved** | **GOLDEN OK**: main's behaviour captured on production's data, then the branch compared against it |
| `python audit.py` | **100 passed, 0 failed, 0 skipped** | **100 passed, 0 failed, 0 skipped** (A-V1: 2,012 agree, 0 disagree, 354 in-play refused, 1,183 pulls) |

Main on the same copy gives 86 passed; the 14 extra checks are exactly the new
`scanner_checks()`. Without `data_golden/` (CI, and any fresh worktree) the suite
is **483 passed, 2 skipped** — CI on Python 3.11 agrees.

## What merging does to production (as shipped)

Nothing, measurably. On fresh copies of production's data, every step of the
scheduled job (`merge_archive`, `finals`, `predict`, `paper`, `market`,
`cronstatus`, `poll --ensure`, `monitor`) was run with main's code and with the
branch's: exit codes and output are identical apart from timings and one new
line, `poll: polling is switched off (config.POLLING_ENABLED = False); nothing
started`. The monitor's ten findings are identical. `poll --ensure` under a
Python audit hook opened no socket, spawned no process, read no registry key,
touched no database and wrote no file. No command adds the seven scanner tables
to production's database (only `python db.py` and `poll --live` call
`db.init()`, and `--live` refuses first). `scoreboard` on the 12-table database
prints a note instead of crashing. The `.bat` edit sits after `git pull`, so
the first run after the merge still executes the old file.

**One thing that is not the branch's doing:** production's working copy has
four tracked files deleted (`docs/briefs/2026-09-22-code-task.md`,
`2026-09-23-next-task.md`, `2026-09-24-next-task.md`,
`2026-09-24-review-task.md`) and an untracked `docs/NEXT_TASK.md`.
`scheduled_check.bat` refuses to run on a dirty tree. Its 11:30 AM ET run did
exactly that (`logs\cronstatus-latest.txt`: "REFUSED TO RUN: this folder has
uncommitted changes … Nothing was pulled, predicted, bet, settled or scored"),
and every run will, merge or no merge, until the owner runs
`git checkout -- docs/briefs` there.

## A1 — venues, one price table, fees, one fair value

**What it does.** Every price source becomes rows of two tables: `markets` (one
venue's contract on one question, keyed `venue|id|type|line`, mapped to the
odds feed's own game row) and `prices` (one side of one market at one moment:
probability, the price exactly as received, size, fee model, capture time).
There is a small translator per venue (sportsbook from The Odds API, Kalshi from
its bids-only book, Polymarket from its book). `fees.py` prices every trade,
and `fair_value()` is the one place a fair price is decided: Pinnacle de-vigged,
else the mean de-vigged across books at the same pull, else the venue's own mid.

**Held:**
- The schema change is **purely additive**. On a copy of production: all 27
  pre-existing schema objects and all 12 tables byte-identical (content hashes),
  7 tables and 6 indexes added, 0 ALTER/DROP/UPDATE/DELETE executed (SQL trace),
  a second `db.init()` changes nothing, and main's code still runs on the
  migrated database (golden OK, audit 84+2 skipped as before).
- `prices` has one writer, `INSERT OR IGNORE`; nothing updates or deletes it.
- American odds convert exactly (+100/−100, −10000, +5000); `price_native` is
  kept as received. On a real 32-game NFL slate: 732 markets, 1,464 prices,
  0 rejects. Kalshi's YES ask = 1 − best NO bid, with that bid's size.
- The `ingest/odds.py` refactor (6739910) changed nothing: old and new code gave
  identical `games`, `odds_snapshots` and output on the same responses;
  "finished is final" and "NULL never overwrites" hold for both callers.
- No market is matched by a date string anywhere in `scanner/`.
- Fees: 415 hand-computed cases agree with `fees.fee`/`fees.ev`, including
  Kalshi's own worked example ($0.005 on 1 @ $0.055). Kalshi's docs were read
  (fee rounding, series fee types); the fee-schedule PDF answers HTTP 429.
- `fair_value` never reads a price captured after `at` (1 µs later is
  ignored; every timestamp shape gives the same answer). Nothing else in
  `scanner/` computes a fair price.
- **A-V1 reproduces, independently.** My own de-vig arithmetic (not
  `bets.engine`) on the production copy: 1,183 pulls, 2,012 agree, 0 disagree,
  354 in-play refused, largest difference 0.0 — exactly the audit's numbers.

**Defects (all confirmed):**
- **In-play prices can be accepted as pregame** (high). A start time stored on
  a market is overwritten by every later sighting, even one made after the game
  began. The Odds API sometimes re-reports a *later* start once a game is under
  way; the cutoff then moves past in-play pulls. Real case: Astros at Rockies,
  2024-04-27 — first pitch 22:05Z, the books repriced the Rockies from +180 to
  +114 after a first-inning home run, and a later pull re-reported the start as
  22:26:59Z; `fair_value` then accepts the 22:25 pull. Over the whole archive, 34
  pulls that were in play by their own report are accepted. The same rule makes
  `fair_value(at)` depend on data captured after `at`, contradicting its
  docstring.
- **The cutoff takes the minimum over books' starts** (medium): a book that
  stopped quoting before a rain delay drags the cutoff back, and `fair_value`
  silently answers with a price up to three hours old. It turns audit's own A-V1
  red on the real week ending 2026-06-06 (1,794 agree, 4 disagree).
- **The exchange-mid fallback has no in-play cutoff** (medium): a Kalshi game
  market returns its in-play midpoint, including when `max_age_s` rejects a
  stale Pinnacle price.
- **Any outcome name returns the home probability** on a sportsbook market
  (medium): `'yes'`, `'draw'`, `''` all give 0.6115.
- **The Kalshi legal kill switch fails open** (medium): `sport` defaults to
  None in the Kalshi adapter, and None means "not sports", so a caller who
  forgets it gets an NFL contract stored, priced and paper-traded with the switch
  off.
- **A totals market with no line crashes the adapter** (medium), losing the whole
  paid pull for that sport.
- **The fee model is not tied to the venue** (medium): a Kalshi price tagged
  `book` is paper-traded with no fee. Negative or NaN fee multipliers are
  accepted. Unknown venues, a size on a sportsbook row, and rows whose
  timestamps later crash the insert all pass the store's checks (low).
- Polymarket's fee is not rounded to 5 decimals as its docs say (low; it is a
  price source only).

## A2 — the polling loop and its credits

**What it does.** A long-running process polls NFL, NBA and NHL from The Odds
API (ten named books = one region, three markets = 3 credits a call) at a
cadence set by the time to the soonest unstarted game, stepping down a fixed
ladder when the day's credits cannot pay for the brief's cadence. Three limits
are checked before every metered call: the brief's 6,000, the ET month's
12,000, and a daily pace. Every call is written to `credit_ledger`. The
scheduled job runs `poll --ensure`, which starts or restarts the loop. It is
switched off (`POLLING_ENABLED = False`).

**Held:**
- `call_cost()` = 3 matches The Odds API's docs ("every group of 10 bookmakers
  is 1 region"; `/events` does not count) and the cloud's own logs (Pinnacle
  plus three books, one market, 1 credit per sport).
- Every number `poll --plan` prints re-derives exactly (e.g. 142.86/day,
  735/week × 42/7 = 4,410; 394.74/day, 2,376/week × 30.4/7 = 10,319).
- A simulated real loop (the real `Poller` and `check()` on a fake clock and
  server) stays inside 6,000 over the brief and 12,000 a month, and still prices
  every game in its last 30 minutes except on days after the brief ran out.
- Failed calls (timeout, 5xx, 429, bad JSON, no headers) are all ledgered at
  their estimate; metered calls are never retried.
- ET midnight, both DST changes and month ends compute correctly.
- The monitor's 25%/10% alerts fire at the right points for the brief, the month
  and the account.
- With the flag off, `--live` refuses before any key read or request; the key
  never appears in output, the ledger, the log or the heartbeat.

**Defects:** (verifier results for this area are in the table below)
- A billed call whose ledger write fails (database busy) is not counted, and the
  loop keeps spending: in a whole-day simulation with the lock held, 375 credits
  billed against a ledger showing 6.
- `check()` then `record()` is not atomic: two loops reach 6,003 of 6,000.
- The off switch does not stop a running loop, and the monitor falls silent
  about it.
- "Restart on new code" never fires: the heartbeat stamps the checkout's current
  commit, not the code the loop loaded.
- The single-loop check is a heartbeat age; a slow tick (378 s against a
  300 s window) makes a live loop look dead. The heartbeat is also stamped with
  the tick's start time.
- The limits and the single-loop check live in each checkout's data folder, but
  dev and production share one paid key.
- `poll --plan` and the docs understate spend: the loop rolls unspent credits
  forward and will use the whole 6,000 and the whole 12,000, not ~4,410 and
  ~10,300.
- In-play prices of already-started games are fetched and stored while any game
  of the sport is still to start; COMMANDS.md says it never polls once a game
  has started.
- Once the brief is spent the loop pauses forever rather than stopping; after
  42 polling days, or on a month's last day, the "pace" offers everything left.

## A3 — capital accounting

`capital.annualize` = ev ÷ days × 365, `days_between` and `resolution_month`
(Eastern) all agree with hand arithmetic, including under a day, zero, negative
and missing. A whole two-level partial fill was recomputed by hand (contracts,
average price, fee, stake, days, fair price, EV, annualized EV): all eight
fields equal. The scoreboard shows raw and annualized EV and capital locked by
strategy and by resolution month; a constructed ledger checks out with zero
mismatches. **Held.** (Per-venue "EV/yr" is a plain average of mixed durations,
which means little; cosmetic.)

## A4 — the strategy registry and per-strategy gates

**What it does.** A strategy is a file in `scanner/strategies/` that registers a
name, venues, a signal, a placebo and a metric (`info` or `realized_ev`). Gate 1
is `record_backtest` against a pre-registered heading; gate 2 is the sports'
unchanged `record_paper` run on the strategy's own positions, at most twice an
ET day, each look logged; gate 3 is `arm()`. Phase A ships no strategies.

**Held:** registration refuses sport names and malformed strategies; nothing
automated calls `arm()`; `arm()` refuses without gates 1 and 2; the scheduled
job's guard (`only_paper_changed`) discards a strategy's gate-2-only change and
refuses a gate-1 change, a new gate-1 block, or arming; exact-match names, so no
strategy sees another's positions; the look cap holds across ET midnight and
both DST days; the sport gates and `bets/engine.py` are unchanged; **the
sequential-testing numbers in docs/gates.md reproduce** (audit's own run
exactly; larger runs 2.24–2.36% for the quoted 2.3%).

**Defects (confirmed, and the most serious in the review):**
- **Gate-2 coverage counts graded-but-unsettled positions** (critical, latent):
  coverage reached 1.0 — and once "6000%" — while the true coverage was 17%;
  gate 2 passed and `arm()` armed. Scanner positions are graded at the start and
  settled later, so this is the normal state, not an edge case.
- **Positions are graded before the game starts and never re-graded** (high):
  `info` becomes drift to the grading moment, not to the fair close; for an order
  placed in the last hour it is exactly 0.
- **The 3-SE bar is not calibrated for `realized_ev`** (high): a no-skill — even a
  money-losing — strategy buying favourites passes gate 2 5–41% of the time
  against the 2.3% the docs claim, because each position's value is one of two
  numbers. Phase B's promo gate, E3 weather and E5 longshot selling all use this
  metric.
- `realized_ev` coverage counts early settlements in the numerator but not the
  denominator (high).
- A placebo that places nothing lets gate 2 pass with no placebo evidence
  (medium); gate 1's check accepts any `#` line in experiments.md, including
  "Results log", and is not tied to the strategy's own entry (medium); a gate-2
  pass survives a new gate-1 record for a different experiment (medium); the
  low-level `record_backtest('mlb', …)` turns MLB's failed walk-forward into a
  PASS (confirmed by one verifier as medium, judged low by the other: nothing
  calls it that way and the scheduled job would refuse the change); a look is
  recorded only after the verdict is written and only if the caller commits
  (low); `strategies` crashes on production's 12-table database once a strategy
  exists (low).

## A5 — paper execution

**Held:** fills use only the first observation strictly after the order (a price
at the order's own microsecond waits; out-of-order inserts never leak a later
price); a maker exactly touched does not fill, and fills at its own price on a
trade-through; a taker at a worse price does not fill; an order bigger than the
size shown fills partially and says so; one order's fills never exceed its size;
nothing fills at or after the start (A-V4 re-derived independently: 3,652
orders, 3,012 fills, 0 at or before the order, 0 in play); the daily cap is
checked before every order, in ET days across DST, with fees; `submit` refuses
every mode but `paper`/`placebo` (13 variants), and the database's CHECK refuses
them too, including through a `str` subclass that fooled `submit`.

**Defects (confirmed):** the same displayed size is filled more than once — five
orders took 150 contracts from one 30-lot, and one maker took 80 from an
unchanged 20-lot seen four times (medium); a maker whose limit crosses the ask
is booked as a maker at its limit with no fee (medium); per-fill cent rounding
can take the stake past the recorded exposure the cap sums (low); orders are
accepted on a market already resolved or started (low); a taker's `expires_at`
is ignored (low); the fee model is taken from the newest price row ever stored,
even one after the order (low); an infinite size raises instead of being
refused (low).

## A6 — tests, audit, docs

**Held:** each of the 14 new audit checks is real; the kill-switch check
exercises the real code paths (five mutations, all caught); the look-cap and
timestamp checks fail on planted bad rows; the unit tests kill 41 of 51
mutations, including every cadence, annualization and look-ahead-in-fills
mutation; the integration test flags an unreachable scanner module and an
undocumented mode.

**Defects:**
- **The "paper only" static scan misses plausible order-sending code** (high):
  11 of 24 shapes missed (`from requests import post as p`, an SDK-style
  `api.create_order(...)`, `subprocess` curl, a URL built from parts…), and it
  skips `tests/`, any top-level name starting with `data`, and every `.bat`.
  With three order-capable plants wired into `scanner/paper.py`, audit still
  printed 100 passed.
- **A-V1 cannot fail on an in-play leak** (medium): with the in-play cutoff
  deleted from `fair_value` it still printed PASS ("0 in-play refused").
- Ten mutations survive the whole suite and the audit, among them look-ahead in
  `fair_value`'s mid path, min→max start, Kalshi cent rounding, the month pace,
  and UTC-day instead of ET-day for both the exposure cap and the look cap
  (medium).
- The "nothing calls arm()" and "Polymarket is a price source" checks test a
  name and a constant, not the path (medium/low); the timestamp check omits
  `paper_positions` and `paper_orders.fair_as_of` (low); the integration test
  wrongly reports a strategy file as unreachable (low).
- Backups: all 19 tables are verified and a planted mismatch in any scanner
  table is refused and the file deleted; a restored backup passes the golden
  test. A backup taken while something writes (Phase C's loop) is deleted as
  "failed" because the source is counted after the copy (low).

## Git history, pre-registration, scope

- **Pre-registration came first.** S0 and A-V1–A-V4 were committed at 00:48,
  before the first code commit (00:51) and before `fees.py` (00:53),
  `budget.py` (01:06) and `paper.py` (01:16). The A-V1 in-play clarification
  (01:00:46) came after `fair.py` existed but before A-V1's first run
  (01:00:57); it narrowed the rule to exclude in-play pulls. A-V1's first run
  failed (10 disagreements) and the results log says so. The S0 fill addendum
  (71e3061) is dated and made the rule stricter.
- No commit silently changes a gate, threshold, key handling, validation.json,
  a workflow or an existing table. `props/collect.py`'s code is unchanged (AST
  identical); only its comments changed. The region-cost correction is right
  per the docs and the cloud's logs.
- Nothing from Phases B–F was built. Every A1–A6 bullet maps to code.
- Kalshi's API docs were read before `kalshi.py`; its terms were not (low).
  `docs/venues.md` says Polymarket's book was "read 2026-09-25" when the fetch
  returned 404 (the shape happens to be right) (low).

## Doc numbers

Confirmed: 100 checks; nineteen tables; 3 credits a call; the Odds API billing
rules; 1 credit per sport in the cloud; 15 books = 2 regions; the Kalshi fee
figures; the Polymarket rates; the gates.md simulation table (except one row);
MIN_PAPER_BETS 50, 3 SE, coverage 0.75, slot 60%/0.90, two looks, 36 h; A-V1
and A-V4 counts; the schedule times in Eastern; the $100 cap.

Wrong: the brief/normal credit projections (~4,410 and ~10,300/13,300: the loop
spends the full 6,000 and 12,000/15,000); "385 passed, 2 skipped" (now 483);
gates.md's 2.9% for re-testing after every position (2.97%, i.e. 3.0%); the
older 1.6–1.8% figures in CLAUDE.md and gates.md are 0.2–0.4 points low
(~1.9–2.0%; still under 5%); "those four" after listing five commands;
CLAUDE.md does not list `scanner/poll.py` as a registry-key reader;
decisions.md calls A-V4's 3,629 fills "orders"; run_daily's usage says only
morning and close spend credits; COMMANDS.md's "never once a game has started".

## Could not check

- Kalshi's fee-schedule PDF (HTTP 429 to every reviewer).
- Whether the detached loop survives the Task Scheduler job that starts it (the
  task has a 15-minute limit; `_spawn` asks to break away from the job and
  silently falls back if refused). Needs polling switched on.
- Whether the monitor raises a false alarm right after a code restart (same).

## Verifier results

The ten reviewers reported 84 defects. Several are the same defect found from
two directions (the coverage flaw three times, the A-V1 in-play gap four
times, the key-reader omission in CLAUDE.md three times). Of the 84, **78 were
confirmed** by an independent verifier, **1 was rejected** (the "free" events
calls: the endpoint is free by the vendor's docs and by production's own
balance, so nothing needs changing), and **5 could not be verified** because
the verifier runs were cut off by a usage limit; those five are re-checked when
they are fixed. "blocks" means the verifier judged it must be fixed before
Phase B is built on this.

| # | area | reviewer's severity | defect | verifiers |
|---|---|---|---|---|
| 1 | A1 schema/venues | medium | The Kalshi legal kill switch fails open: a sports market built without `sport` passes every Kalshi path | reproduce: confirmed, medium |
| 2 | A1 schema/venues | medium | A totals market with no point crashes the sportsbook adapter, losing the whole sport's paid pull | reproduce: confirmed, medium |
| 3 | A1 schema/venues | medium | check_price does not match the fee model to the venue: a Kalshi price tagged 'book' is paper-traded with no fee | reproduce: confirmed, medium |
| 4 | A1 schema/venues | low | The store's checks let through unknown venues and several malformed rows, and some rows that pass then crash the insert | reproduce: confirmed, low |
| 5 | A1 schema/venues | low | No test protects the shared upsert's 'a finished game's date never moves' rule | reproduce: confirmed, low |
| 6 | A1 fees/fair value | high | In-play cutoff trusts the latest reported start: a real in-play pull is accepted as pregame, and fair_value(at) depends on data captured after `at` | reproduce: confirmed, high, blocks; refute: confirmed, medium |
| 7 | A1 fees/fair value | medium | Minimum over books' starts refuses genuinely pregame pulls and silently answers with an hours-old price; A-V1 goes red on real 2026 data | reproduce: confirmed, medium |
| 8 | A1 fees/fair value | medium | The venue-midpoint fallback has no in-play cutoff | reproduce: confirmed, medium |
| 9 | A1 fees/fair value | medium | audit.py's A-V1 check passes when fair_value stops refusing in-play prices | reproduce: confirmed, medium |
| 10 | A1 fees/fair value | medium | fair_value returns the home probability for any outcome name on a sportsbook market | reproduce: confirmed, medium |
| 11 | A1 fees/fair value | low | paper.submit prices fees with a fee model from the future, and orders do not record their fee model | reproduce: confirmed, low |
| 12 | A1 fees/fair value | low | Polymarket fee is not rounded to 5 decimals as its docs specify; odd Kalshi multipliers are accepted | reproduce: confirmed, medium |
| 13 | A2 credits | high | A billed Odds API call can be left out of the ledger, and the loop then keeps spending past every limit | reproduce: confirmed, high, blocks; refute: confirmed, high, blocks |
| 14 | A2 credits | medium | check() then record() is not atomic: two loops or connections can both pass and overspend | reproduce: confirmed, medium |
| 15 | A2 credits | medium | Single-loop guard fails on a slow tick: the heartbeat is stamped with the tick's start time | reproduce: confirmed, medium |
| 16 | A2 credits | medium | poll --plan and the docs understate spend: the real loop uses the whole 6,000 and the whole 12,000 | reproduce: confirmed, medium |
| 17 | A2 credits | low | Once the brief's credits are gone the loop pauses forever instead of stopping | reproduce: confirmed, low |
| 18 | A2 credits | low | 'Free' calls are never checked against the limits and are trusted at 0 when the header is missing | reproduce: rejected, not a defect |
| 19 | A2 credits | low | 'Never once a game has started' is not literally true: each call fetches and stores in-play prices | reproduce: confirmed, low |
| 20 | A2 loop/production | high | The off switch does not stop a running loop: with POLLING_ENABLED False, `poll --ensure` leaves a live loop polling and the monitor goes silent about it | reproduce: confirmed, high; refute: confirmed, high |
| 21 | A2 loop/production | high | 'Restart on newly pulled code' never fires: the heartbeat's code_sha is the checkout's current HEAD, not the code the loop is running | reproduce: confirmed, medium; refute: confirmed, medium |
| 22 | A2 loop/production | high | Credit limits and the single-loop check are per data folder, but dev and production share one paid key: each checkout gets its own 6,000 | reproduce: confirmed, high; refute: confirmed, medium |
| 23 | A2 loop/production | medium | Two loops can run in one data folder, and each passes the credit check before the other records its call (6,003 of 6,000) | reproduce: confirmed, medium |
| 24 | A2 loop/production | medium | 'In play: stop' is per sport only: in-play prices of started games are stored, and COMMANDS.md says 'never once a game has started' | reproduce: confirmed, low |
| 25 | A2 loop/production | low | Restart path spawns even if the old loop has not stopped: the new one refuses and polling is off until the next scheduled run, while ensure reports 'restarted' | reproduce: confirmed, medium |
| 26 | A2 loop/production | low | CLAUDE.md's key-handling paragraph does not list the scanner as a reader of the paid registry key | reproduce: confirmed, low |
| 27 | A3/A5 paper/capital | critical | Gate-2 coverage for info strategies counts graded-but-unsettled positions, so it can exceed 1 and pass the coverage rule and the slot waiver wrongly | reproduce: confirmed, critical, blocks; refute: confirmed, critical, blocks |
| 28 | A3/A5 paper/capital | high | grade() scores a position before its game starts; strategies.run() then never re-grades it, so 'fair close' is whatever pull is newest when a pass lands inside the 60-minute window | reproduce: confirmed, high, blocks; refute: confirmed, high, blocks |
| 29 | A3/A5 paper/capital | medium | The same displayed size is filled more than once: across orders on one snapshot, and by one maker across repeated snapshots of an unchanged offer | reproduce: confirmed, medium |
| 30 | A3/A5 paper/capital | low | A maker order whose limit already crosses the ask is booked as a maker (zero fee, at its limit, on the next poll) instead of the taker fill it would really be | reproduce: confirmed, medium |
| 31 | A3/A5 paper/capital | low | The recorded worst-case exposure (what the daily cap sums) can be smaller than the stake that results, because fees are cent-rounded per fill | reproduce: confirmed, low |
| 32 | A3/A5 paper/capital | low | Orders and fills are accepted on a market whose resolves_at has already passed when it has no event_start | reproduce: confirmed, medium |
| 33 | A3/A5 paper/capital | low | A taker's expires_at is stored but ignored | reproduce: confirmed, low |
| 34 | A3/A5 paper/capital | low | The paper in-play cutoff and fair_value's cutoff use different starts, so a fill can get a stale fair value | reproduce: confirmed, medium, blocks |
| 35 | A3/A5 paper/capital | low | fair_value's venue-mid fallback returns in-play prices, so grade()'s close can include a price captured exactly at the start | reproduce: confirmed, medium |
| 36 | A3/A5 paper/capital | low | submit() picks the fee model from the market's latest price row ever stored, not the latest at `now` | reproduce: confirmed, low |
| 37 | A3/A5 paper/capital | low | An infinite size raises decimal.InvalidOperation instead of Refused | reproduce: confirmed, low |
| 38 | A3/A5 paper/capital | low | Three paper/capital rules are correct but no test would notice if they broke | reproduce: confirmed, low |
| 39 | A3/A5 paper/capital | low | decisions.md mislabels A-V4's fill count as an order count | reproduce: confirmed, low |
| 40 | A4 gates | critical | Gate 2 coverage for an info strategy counts graded-but-unsettled positions, so it exceeds 1.0 and passes (and waives the slot rule) at a true coverage of 17% | reproduce: confirmed, critical, blocks; refute: confirmed, critical, blocks |
| 41 | A4 gates | critical | The 3-SE bar is uncalibrated for realized_ev: a no-skill strategy buying favourites passes gate 2 5.5%-40.6% of the time, and zero spread counts as infinitely significant | reproduce: confirmed, high, blocks; refute: confirmed, high, blocks |
| 42 | A4 gates | high | Positions are graded before the game starts, against a price that is not the close, and never re-graded | reproduce: confirmed, high, blocks; refute: confirmed, medium |
| 43 | A4 gates | high | model.validation.record_backtest has no guard: given a sport name it turns MLB's failed walk-forward into gate 1 PASS | reproduce: confirmed, medium; refute: rejected, low |
| 44 | A4 gates | medium | Gate 1's pre-registration check accepts any '#' line in experiments.md and is not tied to the strategy's own entry | reproduce: confirmed, medium |
| 45 | A4 gates | medium | A gate-2 pass survives a new gate-1 record for a different experiment, so arm() opens at once for an untested strategy definition | reproduce: confirmed, medium |
| 46 | A4 gates | medium | The 'required' placebo is only a required function: one that never places anything lets gate 2 pass with no placebo evidence | reproduce: confirmed, high, blocks |
| 47 | A4 gates | medium | audit.py's 'nothing calls arm()' only finds calls spelled arm(...) in .py files | reproduce: confirmed, medium |
| 48 | A4 gates | medium | Realized_ev coverage counts settled positions that are not yet due (or have no resolves_at), inflating coverage | reproduce: confirmed, high, blocks |
| 49 | A4 gates | low | A gate-2 re-test is not recorded if the database is busy or the caller never commits; two concurrent runs get a third look | reproduce: confirmed, low |
| 50 | A4 gates | low | gates() uses truthiness, so a hand-edited cleared: 'false' or passed: 'no' counts as passed and arm() succeeds | reproduce: confirmed, low |
| 51 | A4 gates | low | No test notices if record_backtest stops disarming on a new result | reproduce: confirmed, low |
| 52 | A4 gates | low | `strategies` crashes on a database without the scanner tables, which is production today | reproduce: confirmed, low |
| 53 | A4 gates | low | Quoted false-pass rates at 10 graded a day are Monte Carlo noise, about 0.2-0.4 points low | reproduce: confirmed, low |
| 54 | A4 gates | low | Scoreboard labels are cut to 26 characters, so paper and placebo rows for sportsbook:williamhill_us look identical | reproduce: confirmed, low |
| 55 | A6 audit/tests | high | The audit's 'paper only' static scan misses plausible order-sending code and skips whole paths | not verified (run cut off); refute: confirmed, medium |
| 56 | A6 audit/tests | high | No test catches look-ahead in fair_value's exchange-mid source | reproduce: confirmed, medium; refute: confirmed, low |
| 57 | A6 audit/tests | medium | The A-V1 audit check passes even when fair_value accepts every in-play price | not verified (run cut off) |
| 58 | A6 audit/tests | medium | Whether the in-play cutoff uses the earliest or latest start any book reports is not tested | reproduce: confirmed, medium |
| 59 | A6 audit/tests | medium | Kalshi fee cent rounding (the default 'conservative' path) is pinned by no test or audit check | reproduce: confirmed, low |
| 60 | A6 audit/tests | medium | No test covers the post-brief monthly daily pace | reproduce: confirmed, low |
| 61 | A6 audit/tests | medium | The ET-day boundary is untested for both the daily exposure cap and the gate-look cap | reproduce: confirmed, low |
| 62 | A6 audit/tests | medium | 'Polymarket is a price source' audit check tests a constant, not the order path | not verified (run cut off) |
| 63 | A6 audit/tests | low | The timestamp-shape audit check omits paper_positions and paper_orders.fair_as_of | not verified (run cut off) |
| 64 | A6 audit/tests | low | The 'nothing calls arm()' audit check matches on the name only | reproduce: confirmed, low |
| 65 | A6 audit/tests | low | The integration matrix doesn't know strategies.load(): a strategy file is reported unreachable | reproduce: confirmed, low |
| 66 | A6 audit/tests | low | Low-impact mutation survivors: fills at exactly the start, and a negative size | not verified (run cut off) |
| 67 | regression | low | A backup is counted against the live database after it finishes, so any write in between deletes a good backup | not verified (run cut off) |
| 68 | regression | low | docs/production-setup.md still quotes the old pytest count (385 passed, 2 skipped) | reproduce: confirmed, low |
| 69 | regression | low | run_daily.py's usage message still says only morning and close spend credits | reproduce: confirmed, low |
| 70 | doc numbers | high | realized_ev coverage counts early-settled positions in the numerator but not the denominator, inflating gate 2's coverage | reproduce: confirmed, critical, blocks; refute: confirmed, high, blocks |
| 71 | doc numbers | medium | Credit projections ignore the loop's own roll-forward pace, understating spend by ~1,600 (brief) and ~1,100-1,700 a month | reproduce: confirmed, medium |
| 72 | doc numbers | medium | 'A daily pace so one day cannot spend the lot' fails on a month's last day once the brief's 42 polling days are used | reproduce: confirmed, low |
| 73 | doc numbers | low | CLAUDE.md says 'Don't run those four in production' after listing five commands | reproduce: confirmed, low |
| 74 | doc numbers | low | pytest count '385 passed, 2 skipped' is stale on the branch (483 passed, 2 skipped) | reproduce: confirmed, low |
| 75 | doc numbers | low | gates.md and scanner/gates.py give 2.9% for re-testing after every position; the doc's own simulation gives 2.97% (3.0%) | reproduce: confirmed, low |
| 76 | doc numbers | low | A-V4 counts disagree across docs (3,652 vs 3,629 'orders'), and the A-V4 script is not in the repository | reproduce: confirmed, low |
| 77 | doc numbers | low | CLAUDE.md's list of scripts that read the paid key from the registry omits the scanner's poll | reproduce: confirmed, low |
| 78 | doc numbers | low | Two imprecise credit/polling sentences: run_daily usage omits poll --live; COMMANDS.md says polling stops 'once a game has started' | reproduce: confirmed, low |
| 79 | history/scope | medium | audit.py's A-V1 check stays green when fair_value uses in-play prices | reproduce: confirmed, medium |
| 80 | history/scope | low | venues.md says Polymarket's order book was read on 2026-09-25; the only fetch returned 404, and the 429 count is off | reproduce: confirmed, low |
| 81 | history/scope | low | Kalshi's terms were not read before the Kalshi adapter was written | reproduce: confirmed, low |
| 82 | history/scope | low | CLAUDE.md does not name the scanner as a new user of the registry-read paid key | reproduce: confirmed, low |
| 83 | history/scope | low | CLAUDE.md says 'those four' after listing five commands that rewrite tracked files | reproduce: confirmed, low |
| 84 | history/scope | low | The reason given for skipping CLAUDE.md's 'ask before changing the schema' overstates what the brief specifies | reproduce: confirmed, low |
