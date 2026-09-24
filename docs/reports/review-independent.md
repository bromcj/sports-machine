# Independent review, Step 1: my own picture of the repo

Written 2026-09-24, 01:00–02:00 ET, **before** opening the handoff, the
cleanup reports, `docs/cleanup-inventory.md` or `docs/integration-matrix.md`.
This is the ground truth the later steps are compared against.

Starting point: commit `5757201` (dev and production identical). 172 tracked
files, 148 of them outside `archive/`, 44,690 lines outside `archive/`.

## How this was done

- **Nothing was run in production.** All commands ran from the dev checkout
  (`C:\Users\BromC\sports-machine-dev`) against a copy of production's `data/`
  in the session scratchpad, via `SPORTS_MACHINE_DATA_DIR`. A second, untouched
  copy of the database was kept for tracing.
- `ODDS_API_KEY` was unset for every command. **That is not enough on its own:**
  `props/collect.py` and two `research/b3` scripts read the key from the Windows
  registry first. None of them was run.
- Eight read-only reviewers read the code subsystem by subsystem (entry points,
  schema, ingest, features/models/gates, bets, operations, tests/audit, git
  history). Every finding below that I report as confirmed was reproduced by a
  query, a run, or by two reviewers independently; the rest are marked.
- One production action: at session start production had an uncommitted
  deletion of `docs/handoff-2026-09-24.md`, which would have made the 11:30am
  job refuse to run. I restored the file from git (nothing lost; it is tracked).

## Entry points, and what each reaches

| Entry point | When | What it runs |
|---|---|---|
| `SportsMachine-CronStatus` task → `scheduled_check.bat` | 11:30am and 10:00pm ET, production folder | refuses on a dirty tree, `git pull`, then `merge_archive` → `run_daily finals` → `predict` → `paper` → `market` → `cronstatus` → `monitor.py` |
| `SportsMachine-Collect` task → `collect_props.bat` | 11:45, 3:45, 6:00, 8:00, 9:00pm ET | `props/collect.py --run` for NFL then NBA. **Spends credits.** No dirty-tree guard, no log, bare `python` |
| `machine_daily.bat` | double-click | `git pull`, `merge_archive`, `backup` |
| `.github/workflows/daily.yml` | 14:13, 21:09, 23:51 UTC (fires 1–4 hours late) | fresh empty DB, `run_daily morning` or `close` (**spends credits**), `export_snapshots`, `healthcheck`, commit `archive/` + `STATUS.md` |
| `.github/workflows/tests.yml` | every push | `pytest tests/` and an import loop |
| `run_daily.py` | by hand | 14 modes: `morning`, `close`, `picks`, `refresh`, `grade`, `predict`, `finals`, `cronstatus`, `market`, `bet`, `shop`, `scoreboard`, `paper`, `backup`. No argument means `morning` (spends credits). Any other word crashes with a `KeyError` traceback |

The scheduled paths reach 37 modules. Manual modes add 5 (`bets.manual`,
`bets.sharp_line`, `features.build_training`, `model.train`, `model.calibrate`).
Nothing reaches `features/sports/nba_features.py`, `nhl_features.py`, the
`props/` research modules, `research/*` or `research/tools/*` except tests and
hand-run scripts. `arm()` is called nowhere in code. `allow_unvalidated=True` is
used only by paper betting, which writes `mode='paper'` rows and cannot stake
money. No code writes a `mode='real'` bet.

## Tables

12 tables, all defined in `db.py`; the live copy matches the code exactly
(tables, columns, the 8 indexes). Row counts on the untouched copy: games
19,579; odds_snapshots 281,688; market_close 7,194; odds_history_progress 4,153;
features 65; predictions 65; probables_history 48; merged_files 24; bets 20
(10 paper, 10 placebo); market_scores 14; api_usage 4; models 3.

- Every stored timestamp in the project's own columns is aware UTC (`+00:00`).
- "Final is final, NULL never overwrites" holds in `ingest/mlb.py`,
  `ingest/scores.py` and `merge_archive.py`. It does **not** hold in
  `backfill.py` (manual only).
- Written but never read by production code: `probables_history` (whole table),
  `odds_snapshots.market_last_update`, most of `models`, `market_scores` apart
  from its losses, and several `bets` columns. Nothing is read but never written.
- `bets.mode` differs between a fresh database (`NOT NULL DEFAULT 'real'`) and
  the live one (plain nullable column added by migration). No NULL rows exist.

## Every command, in the order the scheduled day runs them

Against the copy, 01:24 ET. All exited 0 except the deliberately bad mode.

| # | Command | Exit | What it said |
|---|---|---|---|
| 1 | `python paths.py` | 0 | pointed at the copy |
| 2 | `python db.py` | 0 | no migrations needed |
| 3 | `python merge_archive.py` | 0 | nothing new |
| 4 | `run_daily.py finals` | 0 | 16 + 12 MLB games upserted |
| 5 | `run_daily.py predict` | 0 | 11 of 12 games predicted, Statcast 2 days stale |
| 6 | `run_daily.py paper` | 0 | placed 0; settled 4 (2 graded); gate 2 "2 graded, need 50". **Rewrote the tracked `validation.json`** |
| 7 | `run_daily.py market` | 0 | +1 scored |
| 8 | `run_daily.py cronstatus` | 0 | **"Recent scheduled runs: none yet"** — false, there were five |
| 9 | `python monitor.py` | 0 | 10 checks, all INFO |
| 10 | `run_daily.py picks` | 0 | **every game "no odds feed row within the match window"** |
| 11 | `run_daily.py grade` | 0 | "Last 20 bets, record 6-14, avg CLV −1.38% over 4" — paper and placebo mixed |
| 12 | `run_daily.py scoreboard` | 0 | no manual bets |
| 13 | `run_daily.py shop` | 0 | live collection: 0 games |
| 14 | `run_daily.py shop --historical` | 0 | −4.21/−3.79/−4.35% by book, 1 in 105 games (E4 published −4.24/−3.81/−4.40, 1 in 104) |
| 15 | `python model/validation.py` | 0 | MLB and NFL not cleared |
| 16 | `python bets/engine.py` | 0 | every sport refused |
| 17 | `python healthcheck.py` | 0 | green. **Rewrote the tracked `STATUS.md`** |
| 18 | `python dashboard.py` | 0 | wrote the page |
| 19 | `run_daily.py nosuchmode` | 1 | `KeyError` traceback |

After the run, `git status` in dev showed `validation.json` and `STATUS.md`
modified. In production that means the next scheduled run refuses.

**Dashboard.** The page text has no traceback, `None`, `nan` or `undefined`.
Its numbers match `validation.json` (−0.0082 pooled, t = −4.9, 6,533 games).
Problems: the headline "54.5% picked correctly" comes from a walk-forward run
*without* the home intercept the graded model uses (with it, 55.2% — I
re-derived it); the "road to betting" section says baseball "can sit the same
test football just failed", when it already has and failed; tonight's chart is
empty because no game has a price (see D1). `dashboard.py --png` produced no
image: headless Edge silently fails when the owner's Edge is already open. It
works when given its own profile folder.

**Tests.** `pytest`: 334 passed locally on Python 3.14. **CI has failed on the
last 11 pushes** (Python 3.11): `research/tools/run_all_commands.py:101` uses a
backslash inside an f-string expression, which is a syntax error before 3.12.
`python audit.py`: 86 passed, 0 failed, 0 skipped. Golden test: passes, but it
is skipped in CI (no `data_golden/` there) and pins much less than it says (D24).

## Cleanup history (7dfbe90..5757201, 15 commits in 56 minutes)

Deleted: `features/registry.py` (nothing imported it — safe). Moved:
five B3 one-offs from `props/` to `research/b3/`, and three root markdown files
into `docs/`. The move broke all five B3 scripts when run as scripts (`ROOT`
now points at `research/`). The "31 dead imports" commit briefly left
`features/build.py` unparseable (fixed two commits later, before production).
The command sweep in `b0af441` wrote outside its sandbox: it dumped the whole
database into `archive/` (committed, and the repo is **public**), put copy-DB
backups into the owner's real backup rotation, and committed a `STATUS.md`
from the copy.

## Defects found

Ranked by what they do to the owner. "Verified" means reproduced by query or
run in this session.

### Production is broken, or will be within a day

- **D1. Paper betting has silently stopped, from today.** `merge_archive.py`
  never reads `start_time_utc` from the archive CSVs, so every odds row that
  reaches the laptop only through the archive has no start time, and
  `feeds.odds_twin` cannot match it. Today 0 of 12 games can find their odds;
  yesterday's games only worked because the one-off paid backfill had filled
  their start times. Consequence: no paper bets, no CLV grading, no market
  scoring, from 2026-09-24 on. Gate 2 can never fill. *Verified:* TB@NYY today
  has an odds row with 7 prices and a NULL start; the archive file has the
  start.
- **D2. The nightly paper run makes the next scheduled run refuse.**
  `bets/paper.score()` rewrites `validation.json`, which is tracked; the job's
  guard refuses on any tracked change. *Verified* in dev. It will first bite
  tonight: bets 27, 28, 33 and 34 settle at 11:30am, so the 10pm run refuses,
  and every run after.
- **D3. A refusal is silent.** The refusal branch exits 0, skips `monitor.py`,
  and leaves `ALERTS.md` saying "All clear".
- **D4. CI is red** (above). Nobody noticed because nothing reports it.
- **D5. A `git pull` that changes a running `.bat` scrambles the rest of that
  run** (cmd reads batch files by byte offset). Reproduced by a reviewer in a
  scratch repo. `scheduled_check.bat` was edited six times on 9/23.

### Numbers the owner reads are wrong

- **D6. Live predictions use a different park factor from the one the model was
  trained and graded on.** Live looks up the table by team code (`'COL'`); the
  table is keyed by the Stats API venue number (`'19'`), so live always falls
  back to a hand-typed prior. Two reviewers found it independently; measured
  impact 0.84 win-probability points on average, 5.0 at most. Gate 1 validated
  one pricing function and gate 2 paper-trades another.
- **D7. `cronstatus` says no scheduled runs happened.** It lists the last 20
  runs of *every* workflow, and 20 test runs crowd them out. *Verified.* Its
  "pregame capture" section also shows two fake pulls (the full-database dumps).
- **D8. The grade review mixes paper and placebo bets.** It printed "positive
  CLV — edge is plausible, stay the course" on a mix of one paper bet and one
  random-side bet. It counts only `'W'` as a win, and its 50-bet kill criterion
  cannot fire. *Verified.*
- **D9. Dashboard** (above): wrong-model accuracy, stale roadmap, PNG failure.
- **D10. `export_snapshots.py` says "safe to run locally"; run locally it dumps
  the whole database into `archive/`.** That is how 2 × 41 MB of paid
  historical odds reached the public repo. `archive/` is append-only by the
  owner's rule, so removing them is the owner's call.

### Things that do not do what they say

- **D11. Manual bets (Phase 2) cannot work as documented.** `--game "Falcons at
  Packers"` is refused for every game, because each game has one row per feed.
  With the odds-feed id the fair and best prices are recorded correctly
  (*verified*: Pinnacle de-vigged 0.694, best −250), but that row never gets a
  score, so a moneyline bet never settles; with the score-feed id no price is
  recorded. Spreads, totals and props are graded against the moneyline. A
  typed "push" is recorded as a loss.
- **D12. `bets/sharp_line.py` says flags are logged as `mode='shop'`.** Nothing
  is logged anywhere; `record_bet` would reject `'shop'`.
- **D13. Guardrails check the wrong team's starter** in 6 of 10 paper bets (the
  flags are computed for the side the model favours, the bet goes on the side
  with the bigger edge). The opener test uses batters-faced ÷ 3 as innings,
  which overstates innings by ~40%, so a real opener passes.
- **D14. The props collector's "3,000-credit cap" resets every run.** Ten runs a
  day could each spend 3,000. Its only run so far was fired by hand from the
  dev folder, so its data sits in dev's `data/`, not production's.
- **D15. Backups verify 6 of 12 tables** (not `market_close`, the paid closes
  gate 1 stands on, nor `odds_history_progress`). Backups are not part of the
  scheduled job. The sweep's copy-DB backups sit in the real 14-file rotation.
- **D16. Straight doubleheaders: both games get game 1's odds.** 23 odds rows
  are claimed by two games in `market_close` (the gate-1 baseline). CLAUDE.md
  and README say doubleheaders are refused; the code does not refuse them.
- **D17. `healthcheck`'s "archive exported" check cannot fail in the cloud**
  (checkout resets file times), and a failed `close` pull reports green.
- **D18. The credit monitor watches the wrong key.** The cloud's usage rows die
  with the runner; the monitor reports the local props key (41,742 of 100,000).
- **D19. `market_close` is filled only by a manual script** (last run 9/23), and
  it keeps 18 closes taken more than 60 minutes out.
- **D20. `backfill.py` can wipe a final score** (postponed-then-played game);
  not observed yet.

### The safety net is thinner than it looks

- **D21. The golden test.** It pins 4,000 games that predate all odds, so it
  checks **zero** successful feed matches; its "grading" pin re-reads stored
  columns and runs no grading code; its gate pins read the file, not the gate
  code; its monitor pin depends on the clock and starts failing around 07:05
  this morning with no code change; it is skipped in CI.
- **D22. Tests.** About a third test a copy of the production code written
  inside the test, or a constant. `PAPER_CLV_SIGMA` is not pinned by anything:
  changing 3.0 to 1.0 passes pytest, audit and golden. `test_notify` fires a
  real desktop alert.
- **D23. Audit.** 86 checks, about 11 of which cannot fail or test the audit's
  own copy of the logic (e.g. "predictions are append-only" is
  `COUNT(*) >= COUNT(DISTINCT)`). It leaves temp copies of the database behind
  (1.5 GB found), and will crash for the first weeks of each season.
- **D24. The integration matrix** misses the `backup` mode, cannot follow
  `from ingest import quality`, counts a mode as documented if its name appears
  as any word in five docs, and has no unreachable-module check. It is a report,
  not a check.

### Smaller, or risks rather than defects

- Gate 2's `info` mixes fair sources: in all 4 graded bets the bet-time fair
  price is a soft-book consensus (once a single book) and the close is
  Pinnacle; the switch is 35–45% of each value. This is gate logic, so it is
  reported, not changed.
- Gate 2 has checks that switch themselves off rather than fail (no placebo
  list, short placebo list, missing slots). Not reachable through today's
  caller. Gate 2 is not tied to the model id gate 1 validated.
- Training and live windows disagree the day after an off day (bullpen and
  offense, 11–16% of training rows); doubleheader game 2 gets `rest_days = 0`
  in training, which live cannot produce. Not leakage.
- `notify` reports delivery success whether or not it happened. The 11:30 log's
  dated copy is overwritten by the 10pm one. Paper bets use prices up to 14
  hours old. `run_daily.py refresh` fails silently from another folder.
- Stale docs: README's MLB numbers (−0.0099, t = −5.5, n = 6,519; now −0.0082,
  −4.86, 6,533); "only `morning` and `close` cost anything" (the collector and
  several research scripts spend too); `healthcheck`, `grade`, `paper` and
  `refresh` listed as free *and safe* although they write tracked files;
  several docstrings still say gate 1 runs on a placeholder.

## Checked and true

- **Gate 1 re-derived from raw data** with my own walk-forward code, not the
  project's: pooled −0.008158, SE 0.001680, t = −4.856 over 6,533 games, every
  season to six decimals. Matches `validation.json` exactly. The baseline
  column equals `market_close` for all 6,533 test games.
- **Part E / E5 re-derived from raw tables:** 536 heavy favourites, +2.73
  points, t = +1.44 (published +1.46; bootstrap noise).
- **Grading math:** all 4 graded bets' shop, info, EV, CLV and closing line
  recomputed by hand from stored prices, zero difference; the
  (1+shop)(1+info) = 1+EV identity holds to 1e-16; P&L correct on all 20.
- **One game end to end** (CWS @ KC, 9/23, bets 25/26): right odds record by
  start time (1 minute apart; the previous night's is 1,439 away); status never
  went final-then-not; features and all three predictions made before first
  pitch; the bet used a pregame snapshot; graded against a close 3.6 minutes
  out, same book, Pinnacle fair price.
- **Leakage:** live bullpen features 15 of 15 match my recomputation from raw
  Statcast using only earlier games; starter form 1,010 of 1,010.
- **Gates, by constructed input:** placeholder never clears; gate 2 fails at 49
  bets and passes at 50; coverage 0.74 fails, 0.75 passes; a passing placebo
  blocks; `arm()` refuses without gates 1 and 2; nothing automated calls it.
- **Backups:** the 23:58 backup restored to a temp folder reproduces the golden
  baseline with 0 differences; the newest (00:50) differs only by bets the
  machine settled after 23:58.
- `pytest` 334 passed locally; `audit.py` 86/0/0.

## Could not check

- Whether the cloud's repo secret is the paid 100k key or a 500/month key: the
  cloud's usage is never recorded anywhere I can read.
- Whether The Odds API's terms allow the historical odds now in the public repo.
