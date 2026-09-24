# State of the machine

Re-issued 2026-09-24 (review), after the review's fixes. Plain English, for
someone reviewing this project who has not been living in it. Numbers come from
the code, the committed `validation.json`, and a copy of the live database taken
early on 2026-09-24; where a number will have moved since, it says so.

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

Each run starts from an empty database, pulls, exports what it pulled to
`archive/`, runs `healthcheck.py` and commits `archive/` and `STATUS.md`. The
export and commit run even if an earlier step failed, so paid prices are never
lost with the runner, and a failed odds pull now turns the run red so GitHub
emails the owner. These runs spend the cloud's own key — see *Credits* below.

**GitHub's scheduled runs are hours late, not minutes** — measured on this repo:
a 14:00 cron fired at 17:58, and on 2026-09-23 the 10:13 morning cron fired at
2:22 pm ET. That is why no pull is aimed close to first pitch; a late one would
capture in-play prices, which are worthless as a CLV anchor.

**On this machine (Windows Task Scheduler), twice a day.** One task,
`SportsMachine-CronStatus`, runs `scheduled_check.bat` at **11:30 am** and
**10:00 pm** ET. Both runs do the same steps: merge what the cloud collected,
fetch results, build predictions, place any paper bet that is due, settle
finished ones and re-score gate 2, score finished games against the market, and
write a report and the health checks.

Before any of that it discards the changes the machine makes to tracked files
by itself (`STATUS.md`, and `validation.json` when only its gate-2 block
changed). Any other uncommitted change makes it **refuse loudly**: the log says
REFUSED TO RUN, `ALERTS.md` gets an ERROR, a desktop alert pops, and the task
exits 1. It runs from a copy of itself in `%TEMP%`, so the `git pull` inside it
cannot scramble the file mid-run.

Why the split: placing a paper bet needs the trained model and the Statcast
file, both of which live in `data/`, which is gitignored and must stay out of a
public repo. **The cloud runner physically cannot build predictions.** Gate 2
can only ever be fed from this machine.

A second task, **`SportsMachine-Collect`**, runs `collect_props.bat` at
**11:45, 15:45, 18:00, 20:00 and 21:00** ET. It records NFL and NBA prop and
game prices while they are cheap — live, a request costs 1 credit per market
per region (2 with Pinnacle in the book list, because Pinnacle is in a second
region), where the same snapshot bought historically costs ten times as much.
An idle run spends **nothing**: the events list is free and a game is pulled
only inside its lead window and only once. It spends the **paid** key, stops
before the calendar month's total would pass 3,000 credits, and appends its
output to `logs\collect.log` (the task itself always reports success).

`machine_daily.bat` is the double-click version of the sync, for when you want
to watch it happen. It is also the only automatic backup: the scheduled job
does not take one.

---

## The three gates

`model/validation.py` holds the thresholds, and every one of them was set by
simulation rather than taste, with the reasoning written beside the constant.

**Gate 1 — walk-forward.** Does the model beat the *real de-vigged closing
line*, out of sample, in every test season? Judged on paired per-game log loss,
so a season's margin is measured against its own noise rather than on averages.
It also has to clear the noise when the seasons are pooled
(`WALK_FORWARD_SIGMA = 2.0`, so t > 2), and it must not rest on one season:
dropping any one still has to leave t ≥ 1.

**Gate 2 — paper trading.** After a bet is placed, does the de-vigged price
move the model's way by the close? That movement is `info`; gate 2 tests `info`
rather than raw CLV, because CLV against one book includes line shopping.
`PAPER_CLV_SIGMA = 3.0`, not the usual 2.0, because this gate is re-tested at
every scheduled run and optional stopping turns a 2.5% false-pass rate into 15%.
That figure was simulated for 3 graded bets and one test a day; the job now
tests twice a day ([gates.md](gates.md) says what that means). It also needs 50+
graded bets, `MIN_COVERAGE = 0.75` of settled bets actually graded, no more than
`MAX_SLOT_SHARE = 0.60` of them from one ET time slot (waived above 90%
coverage), and a random-side placebo, run through the same pipeline, that does
**not** pass the same bar.

**Gate 3 — armed.** A human has to call `arm()`. There is no automatic path
from "the numbers look good" to "money moves."

**Current status, read from the committed `validation.json` on 2026-09-24:**

```
mlb: NOT CLEARED - walk-forward FAIL (lost to market in 3 of 3 seasons (2024, 2025, 2026))
                   paper-trading FAIL (only 1 graded paper bets, need 50); armed NO
nfl: NOT CLEARED - walk-forward FAIL (lost to market in 4 of 4 seasons (2022, 2023, 2024, 2025))
                   paper-trading FAIL (nothing recorded); armed NO
```

---

## What is collected

On the copy of the live database taken early on 2026-09-24:

| | |
|---|---|
| games | 19,579 rows (MLB 19,514, NFL 65). One row per feed, so a real game can appear up to three times; 12,129 are finals |
| odds snapshots | 281,688 prices, 4 books, on 7,397 odds-feed games |
| de-vigged closing lines | 7,194 MLB games, Pinnacle on 7,181 (the other 13 a consensus of the rest) |
| NFL receiving props | 169,510 rows, 814 games, 9 books, 597 players |
| Statcast | 3.55 M pitches, 2022–2026 |
| paper bets | 20 (10 paper + 10 placebo), all on 2026-09-23 games; 8 of the 10 paper bets settled, 1 graded |

`archive/` holds append-only CSVs of every price at the moment it existed. It
is committed to git and is the only record of what was available when. It is
never rewritten. Two files in it, `odds-2026-09-24-0419.csv` and `-0425.csv`,
are not pulls: they are 41 MB dumps of a whole local database, committed by a
command sweep that ran `export_snapshots.py` outside the cloud. That script now
refuses to. Whether to remove them is the owner's call.

The closing lines (`market_close`) are filled only by running
`resolve_market_close.py` by hand, last on 2026-09-23. That run predates the fix
that refuses straight doubleheaders, so 23 of its odds rows still serve two
games each, and 18 of its closes were taken more than 60 minutes before first
pitch. Gate 1's numbers above were computed on it as it stands.

---

## What has been answered

Five questions have been asked properly and all five came back negative. Each
was pre-registered in `docs/experiments.md` **before** any model was fit.

| | result |
|---|---|
| **Does the MLB model beat the closing line?** | No. −0.0082 pooled, t = −4.86 over 6,533 games, loses in all three seasons |
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
  bets, need 50`, at 12.5% coverage: of 8 settled paper bets, only 1 could be
  graded (grading needs a close within 60 minutes of first pitch). MLB's regular season ends this
  weekend, so the count will grow through the postseason and then idle until
  March. Expect it to keep reading FAIL or "not enough coverage"; that is the
  gate working, not a fault.
- **Gate 2's simulation was run for a different cadence** (3 graded bets and
  one test a day) from the one the job runs. [gates.md](gates.md) has the
  detail; it should be rerun before gate 2's numbers are leaned on.
- **Gate 1 has not been re-run on a rebuilt `market_close`** (see *What is
  collected*).
- **A properly-timed NFL opener.** The Thursday-noon snapshot was dropped:
  props exist by then but **Pinnacle has not posted them**, so a movement test
  would compare a soft open with a sharp close. When Pinnacle actually posts is
  unknown and costs ~21 credits per candidate time to find out.
- **MLB pitcher strikeouts (B4).** Untested. Preflight found no Pinnacle price
  and only three books, at the same 10 credits per market per event as NFL.
- **Credits.** There are two keys. The cloud's repo secret is on the free
  500-a-month tier; its run log said `Credits left: 480` on 2026-09-24, and the
  three daily pulls project to 372 in October (4 sports × 3 × 31). The paid
  key in the owner's Windows user environment had 41,742 of 100,000 left;
  those do not carry over, and the plan drops to 20K next month. Phase 0
  collection projects **1,850 a month** on the paid key, which fits either.

---

## How to tell if something is broken

Three things, in order of how much they tell you.

**1. `python audit.py`** — must come back **86 passed, 0 failed**. It
re-derives every answer from live data rather than trusting a comment, and it
is the fastest way to know whether a change broke something. It is free and
safe to run any time: it rewrites `validation.json` during its gate checks and
puts it back exactly.

**2. `python monitor.py`** — ten health checks against today's data: games
reaching a final state, no impossible finals, feeds agreeing on first pitch,
paper bets settling, no price used from after first pitch, Statcast currency,
no truncated Statcast game, predictions covering the slate, the mean predicted
home probability, and credits remaining (the paid key's; nothing on this PC
can see the cloud key's). Findings are written to `ALERTS.md` at the repo
root, which every scheduled run rewrites — including one that refuses — and
whose first line is the time it was written.

**The home-probability check.** On the 2026-09-24 copy it read 0.501 over 65
predictions — inside the expected 0.50–0.56, so no warning, but only just. The
7-day window mixed 54 predictions made before the home intercept reached the
saved model (0.489–0.500 a batch) with 11 made after it (0.521); the only model
file carrying an intercept is the one trained at 2026-09-24 03:00 UTC. As the
older predictions age out, the mean should rise toward the new ones. If it
drifts back under 0.50, the intercept is not reaching the serving path.

**Every entry point.** `run_daily.py` has 14 modes: `morning`, `close`,
`picks`, `refresh`, `grade`, `predict`, `finals`, `cronstatus`, `market`,
`bet`, `shop`, `scoreboard`, `paper` and `backup`. It needs one; with none, or
an unknown word, it prints that list and exits. Of these, only `morning` and
`close` spend credits. `tests/test_entry_points.py` checks, in CI, that
every mode is documented in COMMANDS.md and every module is reachable from
an entry point.

`bet` and `scoreboard` are the Phase 2 addition: they record a bet you
placed anywhere and grade it the way the model's paper bets are graded. A
moneyline bet gets the full treatment — fair close, EV after vig, the
shop/info split, coverage, and the same three-gate verdict. Spreads, totals
and props get their result only, and cannot yet be settled in practice
(COMMANDS.md says why). The question they answer is *is this handicapping
worth continuing?*, held to the standard the model is held to. They never
stake money.

**3. `python cronstatus.py`** — did the cloud actually fire, and when. It
counts only the daily workflow's runs, and labels runs from before
`daily.yml` last changed `(old schedule)` rather than attributing them to the
current crons.

**In CI**, `.github/workflows/tests.yml` runs the suite plus a bare-checkout
import check on every push, on Python 3.11. It was red for 11 pushes until
2026-09-24 (a 3.12-only f-string that the owner's 3.14 accepted) and nobody
noticed. `.github/workflows/daily.yml` is the three-times-a-day
collection.

`python -m pytest tests/` gave 385 passed, 2 skipped on 2026-09-24, and needs
no database or network. The two skipped are the golden test, which runs only
where `data_golden/` exists.

**Free and safe any time:** `picks`, `audit`, `dashboard`, `backup`,
`cronstatus`, `validation`, `merge_archive`. **Free, but they rewrite tracked
files:** `healthcheck` (`STATUS.md`) and `grade`, `paper`, `refresh`
(`validation.json`). The scheduled job discards what `healthcheck`, `grade`
and `paper` change; a `refresh` in production changes gate 1, so the next
scheduled run refuses until `validation.json` is reset. **Costs credits:** `morning`, `close`,
`collect_props.bat` / `props/collect.py --run`,
`backfill_odds_history.py --execute`, `ingest/odds.py`, and the
`research/b3` scripts.

---

## Where the documents are

| | |
|---|---|
| [experiments.md](experiments.md) | the pre-registration. Failures stay in it, with numbers. |
| [gates.md](gates.md) | every threshold, and the simulation that set it |
| [decisions.md](decisions.md) | one dated entry per decision — the history |
| [part-e-results.md](part-e-results.md), [b3-results.md](b3-results.md) | the two full write-ups |
| [reports/](reports/) | one per phase of the last brief, plus the 2026-09-24 review (`review-independent.md`, `review-claims.md`) |
| [briefs/](briefs/) | every brief, by date |

## Things this codebase has already got wrong

Worth knowing, because each one was silent and expensive, and each now has a
test or an audit check standing over it.

- **`pandas.sort_values` is not a stable sort.** Assigning a merge result back
  positionally after a re-sort scrambled 98% of the bullpen and offense
  features and went unnoticed for weeks. Join on explicit keys, always.
- **The three feeds mint incompatible game IDs** for the same game. Matching is
  on canonical team names plus first-pitch time, not date strings. A straight
  doubleheader is refused rather than guessed; a split one is told apart by
  time. Until 2026-09-24 straight doubleheaders were *not* refused — both games
  took game 1's odds — and the archive merge dropped start times, so by that
  morning no new game could find its odds at all (both fixed the same day).
- **Team renames lose games silently.** The Athletics dropping "Oakland" cost
  169 games — 7% of a season, and not a random 7%. Every feed join now has a
  canonical name table and a validation step that runs *before* any spend.
- **Train and serve can compute different features under one name.**
  `rest_days` measured days since the last *home* game in training and days
  since any game live; 16% of rows disagreed. Two smaller differences remain:
  after a team's off day, training's 3- and 30-day windows reach one day
  further back than the live ones (the review measured this on 11–16% of
  training rows; not leakage), and until 2026-09-24 live predictions used a
  different park factor from training's.
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
ahead — and find out the answer is no there too. After that, a review on
2026-09-24 found and fixed the machinery around those results (the commits
from `02d2cb9` on); the results themselves reproduced exactly.

The commits worth reading closely are the ones where **a positive result was
taken apart rather than reported**: A1's movement slope (two artifacts, one of
which I initially "fixed" in the wrong direction), E5's favourite–longshot bias
(a book-dependent sample that manufactured a clean-looking gradient), and B3's
vacated-target feature (a column of zeros that made the pre-registered subgroup
test meaningless). All three would have read as findings.

`docs/experiments.md` is the pre-registration. Anything not in it is
exploration, and anything in it that failed is still in it, with the number.
