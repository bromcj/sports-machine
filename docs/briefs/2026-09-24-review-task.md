# Review task: the first outside inspection of this program

## What you're looking at

Everything in this repo was built between 2026-09-21 and today, about four days, by AI coding sessions working from briefs. That includes the data pipeline, the feature code, the models, the three betting gates, the paper-trading and grading logic, the historical odds backfill, the research that produced the "no edge" verdicts, the monitoring, the production setup, and, most recently, a cleanup and reorganization with its own `docs/handoff-<date>.md`.

**None of it has been reviewed by anyone who didn't write it.** The owner is not a programmer and can't check it himself. Earlier reviews were done from outside the repo without access to the database. You are the first reviewer with the code, the data and the ability to run everything. Read the whole system that way: not "is the cleanup tidy" but "does this program do what its documents say it does, and can the owner rely on what it tells him."

The most recent layer, the cleanup, gets the closest look because it moved the most. But a bug in `feeds.odds_twin` or `bets/log.grade` that has been there since day two matters more than a misplaced file, and the gate logic in `model/validation.py` and `bets/engine.py` is the part that stands between the owner and staking real money on nothing.

You did not write any of this (start in a fresh session so that's true), and you don't take its word for anything. Every document in `docs/` is a list of claims. Your job is to find which ones are false.

The output is a plain-English verdict the owner can act on: **safe to rely on**, or **not yet, and here is what's wrong**. Fix what you find; don't just list it.

## Rules

- Same autonomy as the cleanup brief: don't stop for approval. Fix defects as you find them, one commit each, with the evidence in the message. Stop only for the same stop conditions (golden test failure you can't explain, tests or audit red without a logic change, anything that would delete live data), and write `docs/reports/BLOCKED.md` if so.
- Work on a copy of `data/` (`SPORTS_MACHINE_DATA_DIR`), like the cleanup did. Touch live data only in the final production check.
- No new features. No logic changes to gates, models or grading. A review that "improves" things is a different job.

## Method: form your own view first, then compare

**Step 1. Build your own picture of the repo from scratch, before reading any report.** Don't open `docs/handoff-*.md`, `docs/reports/`, `docs/cleanup-inventory.md` or `docs/integration-matrix.md` yet. Instead:

- list every entry point (every `cli` mode, both `.bat` files, both workflows, the scheduled-task entries) and trace what each one reaches, by reading the code, not by running the matrix script;
- list every table and column in `db.py` and find its writers and readers;
- run every command against the copied data, in the order the scheduled day runs them, and record exit codes and output;
- build the dashboard, render it to PNG, and read the rendered HTML text for tracebacks, `None`, `nan`, `undefined`, empty charts, and numbers that don't match a query run the same minute;
- run `pytest`, `python -m audit`, and the golden test;
- read `git log --stat` for the cleanup's commits and list every deleted file.

Write this up as `docs/reports/review-independent.md`. This is your ground truth.

**Step 2. Now read the handoff and the reports, and diff them against your picture.** For every claim in the handoff, mark it **confirmed**, **wrong**, or **couldn't check**, with the evidence. Pay special attention to the places self-authored work goes wrong:

- **The golden test.** Was it captured *before* the first change, on `main`, or after? Check the commit order. Does it cover the outputs that matter (predictions, gate records, feed matches, grading of settled bets), or mostly things that were easy to capture? Does it actually run in CI?
- **The integration matrix.** Does the script find entry points on its own, or was it handed a list? Add a fake unreachable module and a fake undocumented cli mode; the script must flag both. If it doesn't, it's a report generator, not a check.
- **The sweep.** For every deleted file: was it reachable from any entry point at the commit before deletion? Did any test, audit check, `.bat`, workflow or doc reference it? Is the recovery command in `sweep.md` correct (try one)? Was anything *kept* that meets the deletion rule, and if so why?
- **"Every consumer updated."** Grep for every old column and old definition the handoff says was retired (`clv_pct` as a gate input, `snapshot_type='close'` as the closing line, `COUNT(*) FROM predictions` as a game count, and the rest) and confirm no reader still uses them.
- **Tests.** Do the new tests test the code or a mock of it? Does any test pass with the function body replaced by `return True`? Try it on three.
- **Docs.** Every command in every doc exists; every cli mode is documented; no stale count, path, time or gate description. Actually run each documented command line as written.
- **Prose.** Is the prose share under the target because history moved to `docs/decisions.md`, or because it was deleted? Spot-check five decisions that used to be in docstrings and confirm they're in `decisions.md`.
- **Production.** Is the prod checkout on the merged commit? Did the two scheduled runs after merge complete, per `cronstatus` and `monitor`? Does `backup.py` verify the new tables? Is the `.gitignore` change correct (generated files ignored, nothing needed ignored)?
- **Phase 0 collection.** Are the NFL props pulls actually landing (rows with Pinnacle present, injury snapshot alongside, timestamps inside the intended windows)? Is the credit cap enforced in code, and what has been spent so far?
- **Phase 2 and 3.** Enter a manual bet on a real upcoming game, confirm it records the fair and best prices, then delete it. Confirm the shop engine logs flags as `mode='shop'` and never anything else.

**Step 3. Fix and re-verify.** Every "wrong" gets fixed, re-run, and marked. Every "couldn't check" gets a one-line reason.

**Step 3b. Whole-system correctness: trace real rows end to end.** This is the part no one has done. Pick real records and follow them by hand through the code, comparing what the code does with what the docs say it does:

- **One game, ingestion to grade.** Take a finished MLB game from the last week. Find its rows in every feed, confirm the match (`feeds`) picked the right odds record by start time, confirm its status history never went final-then-not-final, confirm its feature row was built only from data dated before first pitch, confirm the prediction stored is the one made before first pitch, confirm the paper bet (if any) used a pregame snapshot and was graded against a close inside the window at the fair price. Every step by query, with the rows quoted.
- **The gates.** Read `bets/engine.evaluate` and `model/validation.py` and confirm, by constructing inputs, that real money is refused unless all three gates pass; that a placeholder baseline can never clear gate 1; that gate 2 needs 50+ graded bets, `info` clear of zero by 3 SE, and coverage; that the placebo must fail; that `arm()` refuses without the first two. Then confirm nothing in the scheduled paths can call `arm()`.
- **Grading math.** Recompute `ev_fair_close`, `shop` and `info` by hand for five settled paper bets from their stored prices and compare to the stored values to 1e-9.
- **The headline results.** Re-derive MLB gate 1 from the `market_close` table and the stored predictions independently of `model/validation.py` (pooled margin, SE, t). It should match `validation.json`. Do the same for one Part E hypothesis of your choice from the raw data.
- **Leakage guards.** Verify one rolling feature from raw Statcast for one team-date the way `audit` claims to, and verify the training table has no row the live path couldn't have produced (1.7 in the cleanup brief).
- **Backups.** Restore the newest backup to a temp path and run the golden test against it.

Anything that doesn't reproduce is a defect, whichever document says otherwise.

**Step 4. Second-pass sweep.** The first sweep was done by the session that wrote the code, which tends to keep things out of caution. Apply the same rule again with fresh eyes: a file stays only if it's on a live path (reached from a `cli` mode, either `.bat`, either workflow or the scheduler), or it reproduces a recorded result in `docs/`, or it tests something kept. Everything else is deleted, not moved. Look especially at: functions with no callers inside kept modules; tests that test nothing that still exists; research scripts whose numbers are already in a results doc; docs that repeat another doc; config entries, imports and requirements nothing uses; comments that narrate history instead of describing the code. Git keeps every version, so deletion is safe. Log every deletion in `docs/reports/sweep-pass2.md` with path, plain-English reason, commit hash, and the recovery command. The golden test, `pytest`, `python -m audit` and the every-command run must all pass afterward; that is the proof nothing live was cut. Under `data/`, report only, delete nothing.

## Output

`docs/reports/review-<date>.md`, in plain English, in this order:

1. **Verdict:** safe to rely on, or not yet.
2. **What was wrong and is now fixed**, one line each, plainest words possible.
3. **What was wrong and is not fixed**, with why, and what it would take.
4. **What was claimed and confirmed**, briefly.
5. **What the second sweep removed**, with a one-line plain-English reason each, and the total: files and lines before and after both sweeps.
6. **The state of production right now**: last two scheduled runs, monitor status, credit spend, backup age.
7. **The folder map**, re-checked: what each folder is for, what runs on a schedule, what is reference, what to open if something looks wrong.
