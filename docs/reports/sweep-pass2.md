# Sweep, second pass (review of 2026-09-24)

The rule: a file stays only if it is on a live path (a `run_daily.py` mode, a
`.bat`, a workflow, the scheduled tasks), or it reproduces a recorded result in
`docs/`, or it tests something kept. Everything else is deleted, not moved.
Git keeps every version; each line below says how to get a file back.

`tests/test_entry_points.py` now enforces the rule in CI: every module must be
reachable from an entry point or be a result-producing script that its results
doc names.

**Proof nothing live was cut** (run after the last deletion): `pytest` 347
passed (golden included); `python audit.py` 86 passed, 0 failed; every command
in the scheduled day's order run on a fresh copy of production's data, all
exit 0 (the two deliberate bad-mode checks print usage and exit 1).

Recovery was tried on one file: `git checkout 1350c12^ -- props/nfl_teams.py`
restores all 69 lines.

## Files deleted (21 files, 4,254 lines)

| Path | Why, in plain English | Commit | Recover with |
|---|---|---|---|
| `research/tools/integration_matrix.py` | Claimed to be a CI check; not run by CI, and passed a fake undocumented mode and a fake orphan module. Replaced by `tests/test_entry_points.py`. | `26640be` | `git checkout 26640be^ -- research/tools/integration_matrix.py` |
| `research/tools/run_all_commands.py` | Its "run against a copy" still wrote to the real `archive/` and the real backup folder. | `26640be` | `git checkout 26640be^ -- research/tools/run_all_commands.py` |
| `research/tools/strip_unused.py` | One-off import remover; broke `features/build.py` once and can silently rename an import. | `26640be` | `git checkout 26640be^ -- research/tools/strip_unused.py` |
| `research/tools/inventory.py` | One-off report generator; nothing uses it. | `26640be` | `git checkout 26640be^ -- research/tools/inventory.py` |
| `research/tools/__init__.py` | Marker for the emptied folder. | `26640be` | `git checkout 26640be^ -- research/tools/__init__.py` |
| `docs/cleanup-inventory.md` | Output of `inventory.py`; not valid UTF-8. | `26640be` | `git checkout 26640be^ -- docs/cleanup-inventory.md` |
| `docs/reports/phase1-inventory.md` | Byte-identical copy of the above. | `26640be` | `git checkout 26640be^ -- docs/reports/phase1-inventory.md` |
| `docs/integration-matrix.md` | Output of the deleted matrix tool; already out of date. | `26640be` | `git checkout 26640be^ -- docs/integration-matrix.md` |
| `docs/reports/command-run-log.md` | Output of `run_all_commands.py`; redone independently in `review-independent.md`. | `26640be` | `git checkout 26640be^ -- docs/reports/command-run-log.md` |
| `research/b3/b3_dryrun.py` | Cost plan for the one-time NFL props purchase, which is done. Could not run since it was moved. | `1350c12` | `git checkout 1350c12^ -- research/b3/b3_dryrun.py` |
| `research/b3/backfill_nfl_props.py` | The purchase script itself (done: 814 of 816 games). Spends credits; could not run since it was moved. | `1350c12` | `git checkout 1350c12^ -- research/b3/backfill_nfl_props.py` |
| `research/b3/check_thursday.py` | One-off check (Pinnacle does not post props by Thursday noon). Spent credits by default. | `1350c12` | `git checkout 1350c12^ -- research/b3/check_thursday.py` |
| `research/b3/preflight.py` | One-off pre-purchase probe. Spent credits. | `1350c12` | `git checkout 1350c12^ -- research/b3/preflight.py` |
| `props/nfl_teams.py` | Team-name table used only by the four scripts above. | `1350c12` | `git checkout 1350c12^ -- props/nfl_teams.py` |
| `docs/results/b3_request_plan.csv` | Output of the dry run; cited by no doc. | `1350c12` | `git checkout 1350c12^ -- docs/results/b3_request_plan.csv` |
| `docs/results/b3_summary.json` | Output of the dry run; cited by no doc. | `1350c12` | `git checkout 1350c12^ -- docs/results/b3_summary.json` |
| `features/sports/nba_features.py` | Never imported; a stub returning all `None`. | `7a0c2cf` | `git checkout 7a0c2cf^ -- features/sports/nba_features.py` |
| `features/sports/nhl_features.py` | Same. | `7a0c2cf` | `git checkout 7a0c2cf^ -- features/sports/nhl_features.py` |
| `NEXT_TASK.md` | Byte-identical to `docs/briefs/2026-09-24-next-task.md`. | `0982a01` | `git checkout 0982a01^ -- NEXT_TASK.md` |
| `docs/handoff-2026-09-24.md` | Older handoff; every section repeats CLAUDE.md, the results docs or the state doc. | `0982a01` | `git checkout 0982a01^ -- docs/handoff-2026-09-24.md` |
| `docs/cleanup-findings.md` | Its main item is now fixed; the rest repeats `phase1.md` or is a note on research code. | `0982a01` | `git checkout 0982a01^ -- docs/cleanup-findings.md` |

## Removed from files that stay

Get any of these back with `git show <commit>^:<file>`.

| Where | What, and why | Commit |
|---|---|---|
| `bets/paper.py` | `summary()`: nothing called it | `a3f2e93` |
| `features/sports/mlb_features.py` | `build_row()` stub and `FEATURE_COLUMNS` (only the stub read it) | `a3f2e93` |
| `features/sports/nfl_features_v1.py` | `build_row()` stub (training uses `build_table`) | `a3f2e93` |
| `feeds.py` | `TERMINAL`, `slot_or_none()`: unused | `a3f2e93` |
| `model/train.py` | `MODEL_VERSION`, the `run_diff_to_win_prob` alias | `a3f2e93` |
| `paths.py` | `ARCHIVE_DIR`, `LOGS_DIR` (five modules build the paths themselves) and the test that only asserted them | `a3f2e93` |
| `props/collect.py`, `props/validate.py`, `research/stats.py` | `ET`; `walk_forward_months()`, `report()`; `passes()`: unused | `a3f2e93` |
| `props/framework.py` | `drop_voids`, `one_bet_per_team`, `devig_two_way`, `edge_vs_market`: called only by their own tests, which went too | `a3f2e93` |
| `bets/guardrails.py` | `CONFIRM_WINDOW_HOURS`, for a late-scratch check never built; the comment claiming it now says what the code does | `a3f2e93` |
| `backfill_odds_history.py`, `audit.py` | an unused constant and four unused imports | `a3f2e93` |
| `config.py` | the `margin` key (read by nothing) and the switched-off `ncaaf`/`ncaab` entries | `7a0c2cf` |
| `.github/workflows/tests.yml` | a hand-kept import list that repeated `tests/test_imports.py` and missed eight modules | `2c9ace5` |
| tests | copies of `enter()`'s checks, self-made arithmetic, a docstring assertion, two string tautologies — replaced by real-code tests or removed | `b0e3359` |
| comments | "Phase N" labels and paragraphs narrating history (details in the commit) | `63c9fc0` |

## Kept although the rule questioned them

- **`research/b3/materialize.py`**: the only code that rebuilds
  `data/props_nfl.parquet` (the input behind `docs/b3-results.md`) from the
  purchased raw files. Fixed so it runs again.
- **`backfill.py`'s `backfill_venues` and `backfill_start_times`**: had no
  caller, but they are the only code that gives historical games a start time
  and a stadium; a rebuild without them cannot match odds. Now called by
  `python backfill.py` (`b2cb7c8`).
- **Research scripts behind recorded results**: `research/part_e_model.py`,
  `part_e_prices.py`, `enrich_statcast.py` (Part E), `a1_movement.py` (A1),
  `d1_calibration.py` (D1), `props/nfl_receiving.py` (B3),
  `features/build_training_nfl.py` (the NFL gate record), and what they import.
  Each results doc now names the script that reproduces it.
- **`model/validation.py:disarm()`**: unused in code but the documented undo
  for `arm()`.
- **`docs/handoff-2026-09-25.md`, `docs/reports/phase0-3.md`,
  `docs/briefs/*`**: dated records this review tested; the claims are marked in
  `review-claims.md`.

## Under data/ — reported only, nothing deleted

- `sports-machine-dev/data/props/raw/` (816 files) and `progress.jsonl`: the
  only copy of about 16,600 credits of purchased props data. **Not in any
  backup** — `backup.py` copies the database only. Worth copying somewhere safe.
- `sports-machine-dev/data/props_nfl.parquet`, `statcast_rich/`,
  `props_live/`: used by the research scripts and the sharp-line report; they
  exist only in the dev folder, not production.
- `sports-machine-dev/data_phase1/` (106 MB): used only by the deleted command
  sweep. Safe to delete by hand.
- `probables_history` table: written on every probables pull, read by nothing.
