# Review step 2: the cleanup's claims, tested

Every claim in `docs/handoff-2026-09-25.md` and the phase reports, marked
**confirmed**, **wrong** or **couldn't check**, with the evidence. Where a claim
was wrong, the last column says what was done. Commit hashes are this
review's. Ground truth: [review-independent.md](review-independent.md).

## The handoff's claims table (§2)

| Claim | Verdict | Evidence | Done |
|---|---|---|---|
| Old brief archived first (`5bac55e`) | confirmed | first commit after `7e6c947`, adds `docs/briefs/2026-09-23-next-task.md` | |
| Prop collection running, costed at 1,850/month | cost confirmed; "running" misleading | `collect.py --plan` prints 1,850. The only paid pull was fired by hand from the **dev** folder; production has no `data/props_live/` | cap and log fixed `86e7e89` |
| Collect task registered after a prod dry run | confirmed, weak proof | the `.bat` ended with `echo`, so it always exits 0 | log added `86e7e89` |
| Inventory generated, not hand-written | confirmed | re-running `inventory.py` on `9587088` reproduces it | tool removed in sweep 2 |
| Golden test pins behaviour | **wrong** | pinned 4,000 pre-odds games (0 matches), re-read stored grading columns, no gate code, clock-dependent, wrote to its own data | rebuilt `ee3ca3d`; 5 of 5 mutations now caught |
| `features/registry.py` deleted | confirmed | nothing imported it | |
| Five B3 scripts moved | confirmed; **the move broke them** | `ROOT` pointed at `research/`; none ran as a script | see sweep 2 |
| One UTC parser | 3 sites confirmed; "one" overstated | `cronstatus.py:100`, `db.py:253` still parse themselves | left: behaviour identical |
| 31 unused imports removed | confirmed | pyflakes 36 → 5; one removed a `noqa`-marked re-export nothing used | |
| `cronstatus.py` crash fixed | confirmed | but it still said "no scheduled runs" | fixed `7f6b9dc` |
| 32 entry points, 0 undocumented | tool output confirmed; **completeness wrong** | 14 modes not 13 (`backup` missed); `market` undocumented; fake orphan module and fake plain-import mode both passed `--check` | replaced by `tests/test_entry_points.py` |
| Every command runs (19, all exit 0) | confirmed as logged; **it wrote outside its sandbox** | exported the whole DB into public `archive/`; backed up the copy into the real OneDrive rotation | export guarded `e2fefe4` |
| Dashboard facts corrected | **partly wrong** | roadmap still said baseball could "sit the test football just failed"; accuracy from a model without the intercept (54.5% vs 55.2%); PNG never rendered | fixed `34e6314` |
| Merged and proved on prod (audit 86) | couldn't check on prod at the time | 86/0 on a copy | re-run in production, see report |
| Gate 2 graded its first bet (n 1, 3.186, placebo −3.318) | confirmed | from a hand run at 00:32, not a scheduled one; "avg_clv" is the info value | |
| Manual scoreboard works | **wrong on real data** | `--game "Falcons at Packers"` refused for every game; no moneyline bet could settle | fixed `f0e14ca` |
| Sharp-line control passes | numbers confirmed; "independent" overstated | shares `novig_probs`; never calls `fair_prob` | docstring fixed `a6bd909` |
| Root tidied to 6 markdown files | confirmed | | |

## Test counts, production state, deletions (§3–§4)

| Claim | Verdict | Evidence |
|---|---|---|
| prod pytest 332 passed, 2 skipped; dev 334 | confirmed locally | **CI was red for 11 pushes** (Python 3.11 syntax) — fixed `a8acc3d` |
| audit 86 passed | confirmed | still 86/0 after every fix |
| "Only one file was deleted" + recovery command | confirmed | `git checkout 6724114^ -- features/registry.py` restores it |
| Two suspected-dead modules kept | confirmed | `export_snapshots.py` is run by `daily.yml` |
| `bets` back to 20 real rows | confirmed | sequence 34 = max id |
| Credits spent this session: 16 | **wrong: 13** | dev `api_usage` 41,755 → 41,742 |
| Newest backup 0050, ~4 min old | confirmed | |
| "0.501 over 65 predictions" | confirmed | |
| `ALERTS.md` "rewritten every run, so never stale" | **wrong** | a refusal skipped monitor and left "All clear" — fixed `20c6385` |
| Added (39) / Modified (31) | headers **wrong** (40 / 32) | the lists themselves are exact |
| `run_daily.py` "11 modes → 13" | **wrong** | 11 → 14 |

## Known defects left in place (§5)

All five confirmed as described. `backfill.py:46` is now fixed (`a0f5389`).

## Judgment calls (§7)

All confirmed as described, except "re-baselined once … the diff was exactly
those two lines": **wrong** — `aa2c1d0` also re-captured `audit_text.json`,
absorbing the full-database dumps, a schema change and a disappeared
healthcheck line.

## Part 2 (§8–§13)

| Claim | Verdict | Evidence |
|---|---|---|
| `predictions.json` 27 entries over two dates | confirmed | |
| `twins.json` ~3,459 rows / "3,459 twin resolutions" | **wrong** | 4,000 rows, 0 matches |
| strip_unused broke `build.py`; guard added | confirmed | the tool can also silently rename an import |
| `manual.py` side detection by substring | confirmed | fixed `f0e14ca` |
| sharp_line parses `stamp[:11]` | confirmed | live scan reproduces 32 games, 15 books |
| §9 admissions (no tests for sharp_line/collect, one Collect run, 455 injury rows) | confirmed | |
| §10 "re-run `run_all_commands.py`" | **unsafe** | it writes to `archive/` and the real backups |
| §13 "the golden test pins current output, not correctness" | understated | it pinned no matches at all |

## Phase reports

| Claim | Verdict | Done |
|---|---|---|
| Phase 0: credit cap "enforced … running total" | **wrong** (reset every run) | `86e7e89` |
| Phase 0: F1 written blind, before any 2026 prop price | couldn't check; timestamps cut against it (F1 committed 23:56:53, first pull 23:53:43) | |
| Phase 1: top-level `.py` 18 → 17 | **wrong** (18 throughout) | |
| Phase 1: history moved to `decisions.md` | **wrong: copied** | 7 of 10 entries still in code; prose grew 2,464 → 2,621 lines. Nothing was deleted without a home (5 checked) |
| Phase 3: "flags are logged separately" | **wrong** (nothing written) | `a6bd909` |
| Phase 3: "fair-line and grading paths are sound" | overreach | the grading math was re-derived by hand in this review and does match |

## Couldn't check

- Anything that needed running in production before this review's final
  check (it was not allowed): see the report's production section.
- The "88 postponed games" history and Phase 1's "4 non-zero exits first
  time" (the first sweep's log was not kept).
