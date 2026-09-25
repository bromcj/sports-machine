# Phase A review: the verdict

Written 2026-09-25 for the owner, in plain English. The detail behind every
line is in [phase-a-review-independent.md](phase-a-review-independent.md)
(what the review found on its own, before reading the handoff),
[phase-a-review-claims.md](phase-a-review-claims.md) (every claim in the
handoff, checked) and the commit messages. Each commit message quotes the
evidence that it was needed and that it worked.

## 1. Verdict

**Safe to build on.**

Phase A had real holes: the review found 84 possible defects and confirmed
78. Some of them would have let a strategy with no skill pass its gates.
Others would have let the polling loop spend past its credit limits, or let
paper orders fill at prices they could never have got. Everything that could
mislead Phase B has been fixed.

The fixes came in three rounds. After each round, separate checkers tried to
break the result again, on copies of production's real data. The last round
found five more problems. One was serious: gate 2 averaged each game's bets,
which let a no-skill strategy that buys again cheaper pass 11 of 20 trials.
The other four were small. All five are fixed, each with a test that failed
before the fix and passes after it. What is still open (section
3) matters only once polling is switched on or exchange trading starts
(Phases C to E). Two items in it are things Phase B itself has to do.

Nothing on this branch can place a real bet, and nothing spends credits
while polling is switched off, which it is. The database refuses any order that is not paper or placebo.
Merging changes nothing production does today: production's own scheduled
steps were run on copies of production's data with both versions, and the
output was identical except for the new commands.

Measured on the final branch:
- **Tests:** 692 passed in the dev folder. Where there is no golden data,
  690 passed and 2 were skipped, the same on GitHub's Linux machine.
- **Golden test:** "GOLDEN OK - nothing moved".
- **Audit:** 100 passed, 0 failed, both in dev and on a fresh copy of
  production's data.
- **Credits:** none spent. The paid key's balance is 41,734, as it was
  before Phase A.

## 2. What was wrong, and is now fixed

Nothing here could have cost money or credits yet: Phase A has no
strategies, polling is switched off, and no code can place a real order.
These are the holes Phases B to F would have been built on.

**The betting gates (the most important part)**
- A strategy's "coverage" could read over 100%. The review armed a strategy
  whose true coverage was 17%. Fixed.
- Positions were graded before their game started, against a price that was
  not the close, and never re-graded. Grading now waits until the market
  has resolved: for a sportsbook game, the start plus a fixed game length,
  moving with any delay.
- **Re-entering a game counted as new evidence.** A no-skill strategy that
  bet each game five times passed gate 2 half the time.
  - The first repair averaged each game's bets into one number. That still
    let a strategy that buys again cheaper after a bad start pass 11 of 20
    no-skill trials.
  - Gate 2 now takes each game's **first** bet only. The same trials: 0 of
    20. In simulation at the real cadence, a no-skill strategy passes about
    1.9% of the time, however often it re-enters.
- Positions that never settled were invisible to coverage. They now count
  against it.
- The profit-based test ("realized EV") let a no-skill strategy that buys
  favourites pass up to 41% of the time. It now cannot pass at all until a
  test built for that kind of payoff is written down in advance (section 3).
- A placebo that placed nothing blocked nothing. It now needs 50 games of
  its own.
- Editing a strategy's file after it passed (a new experiment or measure)
  kept its old pass, and `arm()` would open. The gate record now pins the
  definition, and a new definition needs a new name.
- Smaller fixes:
  - Gate 1 accepted any heading in the experiments file.
  - A gate-2 look could go uncounted.
  - MLB's failed gate 1 could be overwritten by the strategy function, and a
    strategy's gate 1 by the sport function.
  - `strategies` crashed on production's database.

**Prices, and "has the game started?"**
- A start time re-reported *after* a game began turned in-play prices back
  into pregame ones. Real case: Astros at Rockies, 2024-04-27, and 34 such
  pulls in the archive.
- A book that missed the pull announcing a rain delay dragged the game's
  start back. Fair value then answered with prices hours old, which also
  turned the audit's own check red on a real June week.
- The fix: a game has one start, judged pull by pull. Across the whole
  archive (83,294 pulls), the scanner's fair price now agrees with the
  existing sports code on all 141,638 pregame prices and refuses all 24,942
  in-play ones.
- Also fixed:
  - Kalshi and Polymarket midpoints had no in-play cutoff.
  - A nonsense outcome name got the home team's price.
  - A game contract with no known start is now refused rather than priced.
  - A price filed under another venue's market, or under a market that does
    not exist, is now rejected and counted.

**Paper orders**
- One displayed quantity could be filled many times over: five orders took
  150 contracts from an offer of 30. A size shown now fills once per
  strategy.
- A maker order that would really have been a taker was booked at its own
  price with no fee. It is refused now.
- Fee rounding could push a stake past the daily cap. Fixed to Kalshi's own
  rule, and a last fill is no longer refused over a computer's rounding
  noise.
- Orders were accepted after a game's start or a market's resolution, and a
  Kalshi contract could fill 10 minutes into a game. Fixed. A contract that
  resolves before its game starts now stops filling at its resolution.
- Found in the last round: a sportsbook stake with more than six decimal
  places never filled. The limit it is checked against was stored rounded,
  so $25 split three ways was a hair over it and expired every time (about
  30% of random fractional stakes). Phase B's orders are all sportsbook
  orders, so this mattered. Fixed.

**The polling loop and credits** (switched off; these matter for Phase C)
- A paid call whose ledger write failed was never counted, so the loop could
  keep spending past every limit: 375 credits billed and 6 recorded in a
  simulated day. The credits are now reserved *before* the call.
- These were also broken, and are all fixed:
  - Two loops could overspend the cap (6,003 of 6,000).
  - The dev and production folders each got their own 6,000.
  - The off switch did not stop a running loop.
  - "Restart on new code" never fired.
  - A hung loop looked healthy for 8 hours.
  - After the brief's 42 planned days, polling went silent with no warning.
  - A loop killed just after its heartbeat blocked its own restart.
- The loop now runs only in the data folder you mark by hand when you turn
  polling on. The mark names that folder, so a copy of it does not count.
- Found in the last round:
  - Right after a restart, the monitor could tell you to end a process that
    no longer existed. This happened in 6 of 10 real-process trials; it now
    happens in 0 of 20.
  - A mark file written with PowerShell's `>` crashed the command instead
    of being refused.
- The budget projections (~4,410 and ~13,300 credits) were wrong: the loop
  spends up to its caps (6,000 for the brief; 12,000 + 3,000 = 15,000 a
  month). The docs and `poll --plan` now say so.

**The audit**
- Its "no code can place an order" scan missed 11 of 24 ways of writing one.
  It now catches all 24, plus the new ones tried, including files in
  `archive/` and `.pyw` files. CI runs it on every push.
- Its fair-price check could not fail on an in-play price. It now can.

**Tests.** The checkers deliberately broke the code in many small ways to
see whether any test noticed. Four such changes got past every test in the
last round. Three are now caught; the fourth changes nothing (section 4).

**Documents.** 11 wrong or partly wrong claims in the handoff, and stale
numbers across the docs, are corrected with dated notes.

## 3. What is wrong and not fixed, and what it would take

None of this blocks Phase B. Most of it cannot happen until polling is on
or exchanges trade.

**A. Two things Phase B itself must do**
- **A profit-measured strategy cannot pass gate 2 yet.** A promotion's
  value is realized profit, and the 3-standard-error bar lets a no-skill
  favourite-buyer pass up to 41% of the time on that measure. So "realized
  EV" fails closed until a bar built for that payoff is written in
  `docs/experiments.md` before the strategy runs, and simulated like the
  others in `audit.py`'s sequential-testing section.
- **Placebo positions must be settled.** Gate 2 needs the placebo to cover
  50 games of its own. Phase B's settlement code has to settle placebo
  positions as well as real ones, or gate 2 can never pass. That fails safe.

**B. Older problems that main has too (not caused by Phase A)**
- **`validation.json` has no write lock.** Two processes writing at the same
  moment can lose one of the writes, for example a disarm. It would take a
  file lock around the read-and-rewrite in `model/validation.py`.
- **`gates()` counts a hand-typed `"false"` as a pass.** Only reachable by
  editing `validation.json` by hand. It would take a strict true/false
  comparison.

**C. Only once polling is switched on (Phase C)**
- **A restarted loop can die with "database is locked".** This happens when
  new code adds an index to a big table while the monitor is reading. When
  forced, it failed 3 times in 3, and the round-one code did the same. The
  monitor then raises an ERROR, and running `python db.py` once by hand
  clears it. The fix would be to build new indexes in the scheduled job
  before the loop starts.
- **Unverified: whether the loop outlives the scheduled task.** It is
  started detached, but whether Windows ends it along with the Task
  Scheduler job was not tested. It would take one real scheduled run with
  polling on, then `poll --status`.
- **A later start reported during a game, before that new time arrives, is
  believed.** In the whole-archive replay this affected 47 of 206,342 fills
  (0.02%). The fix would store each pull's reported start with its prices.
  That is a database schema change, which needs your OK.
- **In-play prices are stored.** They come free with each call, and every
  reader filters them out. Anything new that reads prices must apply the
  same cutoff. `docs/scanner.md` says so.
- **The mark file names a folder path, not a machine.** A folder restored in
  place, or the same path on another PC, would still count as the one of
  record.
- **The daily pace on a month's last day** allows whatever is left of that
  month's budget. That is still within the month's cap.
- **Cosmetic:**
  - With polling off, a hung loop's ERROR suggests `poll.stop`, which a hung
    loop never reads. Ending the process is the fix, and the ERROR names it.
  - For a moment after a restart, `--ensure` can name the previous loop's
    process number.
  - After the brief's 42 days, the heartbeat can read "running" late in the
    evening while nothing is polled. The monitor's ERROR still fires.

**D. Only once exchanges trade (Phases C to E)**
- **A sports contract not linked to its game can be traded after the game
  has started.** This is a Kalshi or Polymarket contract with no start time
  of its own, which is treated like a weather market. The fix is to link
  every sports contract to its game before trading it (Phase C's mapping),
  or to refuse unlinked ones.
- **Some contracts can be graded too early.** A contract with no resolution
  time, or one set before its game's start, can be graded just after the
  start and keep an early closing price. The fix is to wait until the start
  plus the sport's game length, as sportsbook markets already do.
- **An empty order book makes the maker check refuse an order it should
  accept.** That is the safe direction. Phase E.
- **A replay run backwards in time can fill one offer twice.** This only
  happens when a replay goes back in time between runs; the live loop only
  moves forward.
- **Many resting maker orders will slow the fill check.** Phase E.
- **Kalshi's fee schedule document and terms were not read against the fee
  code** (the brief's C1). They should be read before any Kalshi strategy
  runs.

**E. General**
- **The audit's scan for order-sending code is a tripwire, not a proof.** It
  can raise false alarms, and a deliberately disguised sender could slip
  past. The real protection is that no order key exists anywhere and the
  database refuses anything but paper and placebo.
- **Editing a strategy's code without changing its experiment or measure
  keeps its gate record.** It would take pinning a fingerprint of the
  strategy's file in its gate record.

## 4. What was claimed and confirmed

The handoff made 56 checkable claims:
- **44 confirmed**, several with a caveat.
- **11 wrong or partly wrong**, all corrected in the docs.
- **1 couldn't check**: who deleted production's four brief files. The
  Recycle Bin shows a delete through Explorer at 12:36 AM, which fits the
  handoff's account, but no log proves it.

The main ones that held up:
- **Built on its own branch.** Production was untouched and no API credits
  were spent.
- **Schema.** Seven new tables and nothing existing touched: all 27 of the
  existing definitions and all 12 tables were identical after the upgrade on
  a copy of production.
- **Fees.** Every expected value is after fees. 415 hand-computed cases
  agree, including Kalshi's own worked example.
- **No look-ahead.** Paper orders fill only against prices seen after them;
  every attempt to sneak in an earlier price failed.
- **A real slate.** A 32-game slate from 15 books gives 732 markets, 1,464
  prices and 0 rejects.
- **Pre-registration.** Everything tested against real data was written
  down in `docs/experiments.md` first: committed at 00:48, before the first
  code at 00:51.
- **The polling commands.** `poll --plan` makes no calls, and
  `poll --ensure` on production's data says polling is switched off and
  starts nothing.
- **The scheduled job.** `scheduled_check.bat` is unchanged above its
  `git pull` line, so the running job is not disturbed by the pull.
- **Backups.** They verify all 19 tables and refuse a mismatch, and a
  restored backup passes the golden test.
- **Alerts.** The credit warnings fire as described: a "worth a look" line
  below 25%, a pop-up below 10%.
- **Gate simulations.** The simulations behind the gates reproduce, and
  were rerun for gate 2's first-bet rule: about 1.9% false passes at the
  real cadence.

The one deliberate break that still gets past every test changes nothing:
for a price filed under a market that does not exist, removing that specific
check still rejects the row, only with a different reason.

## 5. Where things stand

- **Branch.** `phase-a-foundations` is pushed. The last code change is
  42184d9; everything after it is tests and documents. GitHub's tests
  passed on it.
- **Merge.** With this verdict, the branch is merged into `main` with
  `git merge --no-ff`. The tests, the golden test and the audit are run
  again after the merge, and then it is pushed. Production picks it up at
  its next scheduled run, but only once the next step is done.
- **Production is refusing to run, and has been since 11:30 AM today.** Its
  folder is missing the four brief files and has an extra copy of the
  scanner brief (`docs\NEXT_TASK.md`), so the safety check stops every run.
  Nothing was pulled, predicted, bet or scored at 11:30. To let it run
  again:

  ```powershell
  git -C C:\Users\BromC\sports-machine checkout -- docs/briefs
  ```

  ```powershell
  Remove-Item C:\Users\BromC\sports-machine\docs\NEXT_TASK.md
  ```

  The first puts the four briefs back from git. The second removes a file
  that is byte-for-byte the same as `docs\briefs\2026-09-25-next-task.md`,
  which `main` already holds, so nothing is lost. The next scheduled run
  (10 PM or 11:30 AM Eastern) then pulls the merge and runs as normal.
- **Polling** stays off (`config.POLLING_ENABLED = False`). Turning it on is
  Phase C's decision: a commit, plus the mark file made by hand in
  production's data folder (COMMANDS.md has the exact line).
- **Next** is Phase B, the promotion and boost engine. It has to start with
  the two items in section 3A.
