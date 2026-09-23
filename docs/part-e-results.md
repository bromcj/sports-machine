# Part E results — screening on 2024–2026

Run 2026-09-23 on 6,519 MLB games with a real de-vigged morning price, closing
price and outcome. Every direction below was written down in
`docs/experiments.md` **before** anything was fit. Raw output:
`docs/results/part_e.txt`.

## Scoreboard

| | hypothesis | predicted | measured | verdict |
|---|---|---|---|---|
| **E1** | market overvalues starters, undervalues bullpens | residual starter terms **negative**, bullpen terms **positive** | best term t = **−1.31**, two of four starter/bullpen terms wrong-signed | **FAIL** |
| **E2** | big starter edge overpriced; tired opposing pen underpriced | gap negative / positive | no bucket clears t = 2 in the predicted direction | **FAIL** |
| **E3** | in a public subgroup the morning price beats the close | at least one subgroup **negative** | every subgroup **positive** (close better); big-market games t = **+2.31**, i.e. the close is *more* dominant | **FAIL** |
| **E4** | NJ books price popular teams **higher** than Pinnacle; gap bigger on weekends and further out | positive | popular teams priced **lower**, t = −7.4 to −9.5; weekends flat; no time pattern | **FAIL** (all three) |
| **E5** | heavy favourites win more than implied | positive, t > 2 | **+2.73 points, t = +1.46** | **FAIL** |
| **E6** | residual more predictable in April–May; velocity drop predicts underperformance | early > late; velocity coef positive | early +0.0028 vs late +0.0024 (same); velocity April–May **wrong-signed**, t = −0.40 | **FAIL** |

**Zero of six pass.** Nothing was re-tuned, no threshold was moved, and no
season was dropped.

Because nothing passed, **no vig-adjusted EV is owed for a passing hypothesis**.
The EVs computed anyway, for the strongest near-misses, are all below the vig:
E1's best residual term is worth 1.30 probability points per standard deviation
against a ~2-point break-even, and E5's heavy favourites returned **+1.53%** at
Pinnacle (t = 0.58) and about zero at all three NJ books.

## E1 in detail — the central test

The claim that the market overvalues starting pitchers is the most repeated
idea in baseball betting, so it got the most care. New measures were built
first (`research/pitching.py`): a Marcel-style starter projection blending
prior season and season-to-date K-BB% and **xwOBA-against**, shrunk by batters
faced; a **leverage-weighted** 30-day bullpen K-BB% using Statcast's
`delta_home_win_exp`; and fatigue from the top three leverage arms' pitches in
the prior two days. A leakage self-check recomputes sample rows by brute force
and passes.

The decisive regression is `outcome ~ offset(logit p_close) + features` — does
a feature predict the result **on top of** the closing price?

| feature | coef | 95% CI | t | predicted |
|---|---|---|---|---|
| starter K-BB% edge | +0.0122 | [−0.067, +0.092] | +0.30 | negative — **wrong sign** |
| starter xwOBA edge | −0.0520 | [−0.132, +0.029] | −1.31 | negative — right sign, n.s. |
| bullpen quality edge | −0.0170 | [−0.072, +0.037] | −0.61 | positive — **wrong sign** |
| opponent bullpen fatigue | −0.0192 | [−0.077, +0.034] | −0.66 | positive — **wrong sign** |
| opponent arms on a 3rd day | +0.0022 | [−0.053, +0.058] | +0.07 | positive — n.s. |

And the two recipes agree. The market's weight on starter quality (+0.111) and
reality's (+0.122) differ by −0.011, t = −0.27; on bullpen quality, +0.069
against +0.051, t = +0.64. **The market is weighting starters and bullpens
about as heavily as the outcomes say it should.**

One caveat recorded honestly: on the ~2,950 games that also carry a velocity
change for both starters, bullpen fatigue reaches t = −2.27 — but with the
**opposite sign** to the prediction, which the pre-registered rule counts as a
failure, not a discovery. Chasing it would be exactly the behaviour this file
exists to prevent.

## What the nulls are worth, and what they are not

E5 was included to calibrate power, and it did its job. With 536 heavy
favourites the standard error on a win rate is 2.16 points, so a real 2–3 point
bias is simply invisible. **A null from E2, E3, E5 or E6 means "no large
effect", not "no effect."** E1 and E4 are better powered and their nulls are
correspondingly stronger.

E4 deserves a separate note because it measures something no other test does.
Betting every NJ price against Pinnacle's de-vigged number returns **−4.24%
(DraftKings), −3.81% (FanDuel), −4.40% (BetMGM)** per unit. Inside 90 minutes
of first pitch, prices worth more than 1.5% EV appear **38 times in 27,196 at
DraftKings, 18 in 27,202 at FanDuel, 35 in 27,170 at BetMGM** — roughly one per
eighty games per book. This is a lower bound, because we hold four books and a
real shop watches eight to fifteen, but it is a hard number and it sets
expectations for Part C.

## Why the confirmation seasons are not worth buying

The two-stage design says: screen on 2024–2026, confirm on 2022–2023 for
anything that passes. Nothing passed, so there is nothing to confirm.

The stronger argument is that buying them would not rescue the near-misses
either. Those seasons would take n from 6,519 to about 10,600, a factor of 1.63,
and t grows with the square root of n:

| near-miss | t now | t with both seasons bought |
|---|---|---|
| E1 starter xwOBA residual | 1.31 | **1.67** |
| E5 heavy favourites | 1.46 | **1.88** |
| E6 velocity, whole season | 1.31 | **1.67** |

None of them crosses 2. **About 28,000 credits would buy three results that
still say "no edge found"** — and that assumes each effect is real and merely
under-measured, which is the most generous reading available.
