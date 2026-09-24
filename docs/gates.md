# The gates, and why each threshold is what it is

Every constant in `model/validation.py` was set by simulation, not taste. The
simulations used to live in comment blocks beside the constants; they are here
now so the code can say what it enforces and this can say why. **If you change
a sigma, rerun the simulation in `audit.py`'s SEQUENTIAL TESTING section** —
these numbers are empirical, not constants of nature.

## The three gates

| gate | passes when | recorded by |
|---|---|---|
| `walk_forward` | the model beats a **real** de-vigged market in every test season, the pooled per-game margin is more than `WALK_FORWARD_SIGMA` = 2 SE above zero, and with three or more seasons, dropping any one still leaves t ≥ 1 | `record()` |
| `paper_trading` | 50+ graded paper bets whose mean **info** clears zero by `PAPER_CLV_SIGMA` = 3 SE; coverage (graded / settled) ≥ 0.75; no start-time slot over 60% of graded bets unless coverage ≥ 0.90; and the random-side placebo must **not** clear the same bar | `record_paper()` |
| `armed` | a human calls `arm()`, which refuses until the first two pass | `arm()` |

**Info, not CLV.** CLV against the same book's close has the vig in it and
rewards line shopping as much as forecasting. Each bet is split into `shop`
(the price taken against the fair price at the time) and `info` (the de-vigged
closing price against the de-vigged price when the bet was placed); gate 2
tests `info`. The constant keeps its old name, `PAPER_CLV_SIGMA`.

Beating a **placeholder** baseline — a home-rate constant, a fixed 54% — clears
nothing, however large the margin. No record means not cleared, so a fresh
checkout or a cloud runner refuses by default.

## Why the two sigmas differ: 2.0 and 3.0

`WALK_FORWARD_SIGMA = 2.0`, `PAPER_CLV_SIGMA = 3.0`. The difference is the
difference between testing once and testing every night.

Gate 1 is recomputed only when someone deliberately reruns training, on the
same fixed set of seasons. Rerunning does not generate new evidence and does
not give the result new chances to pass, so the conventional ~95% two-sigma bar
is right.

Gate 2 is re-tested **every night** as paper bets accumulate, and each night is
a fresh opportunity to cross the bar by luck. That is optional stopping, and it
is not a small effect. Simulated on a zero-skill model with this project's own
CLV spread, 3 bets a day:

| testing | 30d | 60d | 120d | 180d | 365d |
|---|---|---|---|---|---|
| once at the end | 1.8% | 2.5% | 2.1% | 2.8% | 2.2% |
| **every night** | 5.5% | 10.4% | 13.2% | **15.3%** | 16.7% |

**Two sigma checked nightly is a 15% false-pass rate, not 2.5%** — six times
looser than it looks. Raising the bar restores it, at a cost measured rather
than guessed (180-day season, nightly):

| sigma | false pass | detects a real +0.5% edge |
|---|---|---|
| 2.0 | 13.6% | 99.1% |
| 2.5 | 5.2% | ~95% |
| **3.0** | **1.6%** | **88.4%** |
| 3.5 | 0.3% | 74.5% |

3.0 buys back the guarantee for about 11 points of power, which is the right
trade when the downside is staking money on a model with no edge.

**Calibrated to a nightly cadence.** If `score()` ever runs more often than
once a day, or paper volume per day changes a lot, rerun the simulation.

**Both of those have happened, and the simulation has not been rerun.** Every
number on this page (and the SEQUENTIAL TESTING check in `audit.py`, which
uses `per_day=3` and tests once per simulated day) assumes **3 graded bets a
day, tested once a day**. The running job is different:

- `score()` runs **twice a day**. `scheduled_check.bat` calls
  `run_daily.py paper` at 11:30am and again at 10pm, and each call re-tests
  gate 2 — twice as many looks as the simulation gave luck.
- Volume is not 3 graded bets a day. The job places up to one paper bet per
  game with a price — 10 for 2026-09-23's games, the first day (9 placed by
  a hand run, 1 by the 11:30 job) — but only a bet whose
  game has a close within 60 minutes of first pitch is graded, and so far
  that is 1 of 8 settled (coverage 12.5%, `validation.json`).

Which way the net effect goes — more looks, a different number of new bets
between them — has not been measured. Until the simulation is rerun for this
cadence, treat the false-pass and power figures here as approximate. Nothing
on this page, and no sigma, has been changed for it.

## What gate 2 costs in time

A small edge needs a lot of evidence. Bets required to detect a real edge 80%
of the time, nightly testing at sigma 3.0:

| true edge | bets | days at ~3/day |
|---|---|---|
| +1.0% | ~121 | ~40 |
| +0.5% | ~454 | ~151 |
| +0.25% | ~1,764 | ~588 |

Months, not weeks. That is the correct trade: the cost of passing a model with
no edge is losing money indefinitely; the cost of making a good model wait is
waiting. (The "days" column assumes 3 graded bets a day; at the coverage
measured so far, fewer are graded than that — see above.)

The 50-bet floor stays as a separate, independent condition — a handful of
lucky bets can clear a t-statistic, and n is the cheaper guard.

## The other thresholds

| constant | value | why |
|---|---|---|
| `MIN_COVERAGE` | 0.75 | graded / settled. Below this, gate 2 fails: an info average over a quarter of the sample is not the sample. |
| `MAX_SLOT_SHARE` | 0.60 | no more than 60% of graded bets from one start-time slot, so the gate cannot be cleared by one favourable time of day. |
| `COVERAGE_WAIVES_SLOTS` | 0.90 | above 90% coverage the slot rule is waived — at that point the sample is the slate, not a selection from it. |

## The rule underneath all of it

Edge is not "my number differs from the market's." It is "my number is
**better** than the market's."

The NFL v1 model was well calibrated, beat the home baseline decisively, and
hit 62–66% accuracy — and lost to the closing line in all four test seasons.
Run its picks through a 4% edge threshold and it fires on 74% of games, claims
a +12.5% mean edge, and returns **−9.3%** over 808 simulated bets.

When the forecaster is worse than the price it is betting into, disagreement is
noise, and the threshold sells it back as confidence.
