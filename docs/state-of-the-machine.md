# State of the machine

Re-issued 2026-09-24, against the cleaned tree. Plain English, for someone reviewing this project who has
not been living in it. Everything here was read off the running system rather
than remembered.

---

## What it is, in one paragraph

A multi-sport betting model that **refuses to bet until it can prove it beats
the bookmakers**. It collects prices and results every day, retrains, and runs
three gates. Until all three pass, `bets/engine.py` returns `bet: False` for
every wager, and no amount of apparent edge changes that. The refusal is the
product. As of today **both sports are refused**, and that is now a measured
verdict rather than a default.

---

## What runs, when, and where

**In the cloud (GitHub Actions), three times a day.** Times are UTC because
GitHub cron has no timezone and no DST awareness; the ET equivalents are for
this half of the year.

| UTC cron | ET | mode | what it does |
|---|---|---|---|
| `13 14 * * *` | 10:13 am | `morning` | opening lines, once probables are up |
| `9 21 * * *` | 5:09 pm | `close` | ~2 h before the 7:05 pm main slate |
| `51 23 * * *` | 7:51 pm | `close` | ~2 h before 9:40 pm west-coast games |

The mode is chosen by matching the **hour only** (`*' 14 * * *'` → morning).
It used to compare the whole cron string, which meant changing a minute would
silently have turned the morning pull into a close pull.

**GitHub's scheduled runs are hours late, not minutes** — measured on this repo:
a 14:00 cron fired at 17:58. That is why no pull is aimed close to first pitch;
a late one would capture in-play prices, which are worthless as a CLV anchor.

**On this machine (Windows Task Scheduler), twice a day.** One task,
`SportsMachine-CronStatus`, runs `scheduled_check.bat` at **11:30 am** and
**10:00 pm** ET. It merges what the cloud collected, places or settles paper
bets, and writes a report.

Why the split: placing a paper bet needs the trained model and the Statcast
file, both of which live in `data/`, which is gitignored and must stay out of a
public repo. **The cloud runner physically cannot build predictions.** Gate 2
can only ever be fed from this machine.

A second task, **`SportsMachine-Collect`**, runs `collect_props.bat` at
**11:45, 15:45, 18:00, 20:00 and 21:00** ET. It records NFL and NBA prop and
game prices while they are cheap — a live request costs 1 credit per market,
the same snapshot bought historically costs 10. An idle run spends **nothing**:
the events list is free and a game is pulled only inside its lead window and
only once.

`machine_daily.bat` is the double-click version of the sync, for when you want
to watch it happen.

---

## The three gates

`model/validation.py` holds the thresholds, and every one of them was set by
simulation rather than taste, with the reasoning written beside the constant.

**Gate 1 — walk-forward.** Does the model beat the *real de-vigged closing
line*, out of sample, in every test season? Judged on paired per-game log loss,
so a season's margin is measured against its own noise rather than on averages.
`WALK_FORWARD_SIGMA = 2.0`.

**Gate 2 — paper trading.** Does it earn positive closing-line value on bets
actually placed before kickoff? `PAPER_CLV_SIGMA = 3.0`, not the usual 2.0,
because this gate is **re-tested every night** and optional stopping turns a
2.5% false-pass rate into 15%. Also requires `MIN_COVERAGE = 0.75` of bets
actually graded, and no more than `MAX_SLOT_SHARE = 0.60` of them from one time
slot — waived above 90% coverage.

**Gate 3 — armed.** A human has to call `arm()`. There is no automatic path
from "the numbers look good" to "money moves."

**Current status, read from the system today:**

```
mlb: NOT CLEARED - walk-forward FAIL (lost to market in 3 of 3 seasons)
                   paper-trading FAIL (nothing recorded); armed NO
nfl: NOT CLEARED - walk-forward FAIL (lost to market in 4 of 4 seasons)
                   paper-trading FAIL (nothing recorded); armed NO
```

---

## What is collected

| | |
|---|---|
| games | 19,555 (MLB 19,490, NFL 65) |
| odds snapshots | 281,688 prices, 4 books |
| de-vigged closing lines | 7,194 games, Pinnacle on 7,020 of 7,032 |
| NFL receiving props | 169,510 rows, 814 games, 9 books, 597 players |
| Statcast | 3.55 M pitches, 2022–2026 |
| paper bets | 20 (10 paper + 10 placebo); 1 graded so far |

`archive/` holds append-only CSVs of every price at the moment it existed. It
is committed to git and is the only record of what was available when. It is
never rewritten.

---

## What has been answered

Five questions have been asked properly and all five came back negative. Each
was pre-registered in `docs/experiments.md` **before** any model was fit.

| | result |
|---|---|
| **Does the MLB model beat the closing line?** | No. −0.0099 pooled, t = −5.5, loses in all three seasons |
| **Does it beat the *opening* line?** | No, by nearly as much — so it is not a timing problem |
| **Does it know anything the 10 a.m. price doesn't?** (A1) | A little. It predicts line movement (+0.0241, t = +6.5) — worth 0.17 probability points against a ~2-point break-even. **Twelve times too small** |
| **Is the market itself structurally wrong?** (Part E) | Six named hypotheses, **zero passed**. The market's weights on starters and bullpens match reality's within noise |
| **Do NFL receiving props beat the price?** (B3) | No. −0.011 to −0.022, t = −5.4 to −11.2, under both de-vigging choices — and it loses to a player's own season average too |

The honest summary: **every feature these models use is already in the price.**
That is the normal result for public-data models, and the gates stopped us from
paying to learn it.

The two things worth remembering from the failures: **props carry far more vig
than sides** (5.6–7.1% against 3.9–4.6%), so "softer market" is true of the
*line* and not of the *price*; and **E4 found almost nothing to shop** —
betting every NJ price against Pinnacle returns −3.8% to −4.4%, with a
worthwhile price appearing about once in a hundred games.

---

## What is open

- **Gate 2 is running for the first time.** It records `only 1 graded paper
  bets, need 50` — the pipeline settling, grading and scoring a real bet end to
  end, which had never happened before. MLB's regular season ends this weekend,
  so the count will grow through the postseason and then idle until March.
  Expect it to keep reading FAIL or "not enough coverage"; that is the gate
  working, not a fault.
- **A properly-timed NFL opener.** The Thursday-noon snapshot was dropped:
  props exist by then but **Pinnacle has not posted them**, so a movement test
  would compare a soft open with a sharp close. When Pinnacle actually posts is
  unknown and costs ~21 credits per candidate time to find out.
- **MLB pitcher strikeouts (B4).** Untested. Preflight found no Pinnacle price
  and only three books, at the same 10 credits per market per event as NFL.
- **Credits:** ~41,700 left this billing month; they do not carry over, and
  the plan drops to 20K next month. Phase 0 collection projects **1,850 a
  month**, which fits either.

---

## How to tell if something is broken

Three things, in order of how much they tell you.

**1. `python audit.py`** — must come back **86 passed, 0 failed**. It
re-derives every answer from live data rather than trusting a comment, and it
is the fastest way to know whether a change broke something. It is free and
safe to run any time.

**2. `python monitor.py`** — ten health checks against today's data: games
reaching a final state, Statcast currency, predictions covering the slate,
paper bets settling, no price used from after first pitch, feeds agreeing on
first pitch, credits remaining. Findings are written to `ALERTS.md` at the repo
root, which is rewritten every run so a stale file can never masquerade as a
current problem.

**There is one live WARNING right now and it is expected:** *"mean predicted
home probability is sane: 0.497 over 54 predictions (expect 0.50–0.56)"*. Those
54 predictions were made **before** the home intercept was applied today. New
ones should land near 0.53. If that warning is still there in a week, the
intercept is not reaching the serving path and something is wrong — which is
exactly the kind of drift the check exists to catch.

**Every entry point** — all 29 of them, with the tables each reads and writes
and the docs and tests covering each — is in
[integration-matrix.md](integration-matrix.md), regenerated by a script so
drift shows up as a diff. `--check` fails if a mode exists without being
documented. The modes are `morning`, `predict`, `paper`, `close`, `finals`,
`grade`, `market`, `picks`, `refresh`, `backup`, `cronstatus`, `bet`
and `scoreboard`.

`bet` and `scoreboard` are the Phase 2 addition: they record a bet you
placed anywhere and grade it on exactly the terms the model's paper
bets are graded on — fair close, EV after vig, the shop/info split,
coverage, and the same three-gate verdict. The question they answer is
*is this handicapping worth continuing?*, held to the standard the
model is held to. They never stake money.

**3. `python cronstatus.py`** — did the cloud actually fire, and when. It knows
the schedule changed recently and labels older runs `(old schedule)` rather
than attributing them to the current crons.

**In CI**, `.github/workflows/tests.yml` runs the suite plus a bare-checkout
import check on every push; `.github/workflows/daily.yml` is the three-times-a-
day collection. Both are in the integration matrix.

`python -m pytest tests/` gives 334 passing and needs no database or network.

**Free and safe any time:** `picks`, `audit`, `dashboard`, `grade`, `refresh`,
`backup`, `cronstatus`, `validation`, `healthcheck`, `merge_archive`.
**Costs credits:** only `morning` and `close`.

---

## Where the documents are

| | |
|---|---|
| [experiments.md](experiments.md) | the pre-registration. Failures stay in it, with numbers. |
| [gates.md](gates.md) | every threshold, and the simulation that set it |
| [decisions.md](decisions.md) | one dated entry per decision — the history |
| [integration-matrix.md](integration-matrix.md) | every entry point, what it touches, what covers it |
| [cleanup-findings.md](cleanup-findings.md) | things that look wrong and were left alone, with file:line |
| [part-e-results.md](part-e-results.md), [b3-results.md](b3-results.md) | the two full write-ups |
| [reports/](reports/) | one per phase of the last brief |
| [briefs/](briefs/) | every brief, by date |

## Things this codebase has already got wrong

Worth knowing, because each one was silent and expensive, and each now has a
test or an audit check standing over it.

- **`pandas.sort_values` is not a stable sort.** Assigning a merge result back
  positionally after a re-sort scrambled 98% of the bullpen and offense
  features and went unnoticed for weeks. Join on explicit keys, always.
- **The three feeds mint incompatible game IDs** for the same game. Matching is
  on first-pitch time, not date strings, and doubleheaders are refused rather
  than guessed.
- **Team renames lose games silently.** The Athletics dropping "Oakland" cost
  169 games — 7% of a season, and not a random 7%. Every feed join now has a
  canonical name table and a validation step that runs *before* any spend.
- **Train and serve can compute different features under one name.**
  `rest_days` measured days since the last *home* game in training and days
  since any game live; 16% of rows disagreed.
- **`min_edge` measures disagreement, not edge.** It only pays when the model
  forecasts better than the price. The NFL model picks 62–66% of games right,
  claims +12.5% mean edge, and returns −9.3% over 808 simulated bets.
- **A "probability" that is really a price.** De-vigging has to happen in one
  place; otherwise a difference between two books turns out to be a difference
  between two code paths.

---

## If you are reviewing the last ~30 commits

The arc is: prove the model against a real market (Phase 5), find out the
answer is no, then test three specific places a small operation might still be
ahead — and find out the answer is no there too.

The commits worth reading closely are the ones where **a positive result was
taken apart rather than reported**: A1's movement slope (two artifacts, one of
which I initially "fixed" in the wrong direction), E5's favourite–longshot bias
(a book-dependent sample that manufactured a clean-looking gradient), and B3's
vacated-target feature (a column of zeros that made the pre-registered subgroup
test meaningless). All three would have read as findings.

`docs/experiments.md` is the pre-registration. Anything not in it is
exploration, and anything in it that failed is still in it, with the number.
