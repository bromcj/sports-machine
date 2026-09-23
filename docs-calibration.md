# 3.6 Home-team calibration — findings and a proposal

**Report only. Nothing in the model was changed.**

## The finding

`margin_to_win_prob` is `p = sigmoid(k · margin)`, which has no intercept, so
it returns exactly 0.500 whenever the predicted run margin is zero. Measured
out of sample across the three test seasons:

| season | n | mean predicted | actual home win rate | gap |
|---|---|---|---|---|
| 2024 | 2167 | 0.498 | 0.521 | **−0.022** |
| 2025 | 2173 | 0.504 | 0.546 | **−0.042** |
| 2026 | 2116 | 0.507 | 0.531 | **−0.024** |
| pooled | 6456 | 0.5030 | 0.5324 | **−0.0294** |

The model under-rates home teams by about 2.9 percentage points, every season,
in the same direction.

## It is an intercept, not a slope

A reliability table over the pooled out-of-sample predictions:

| predicted bucket | n | predicted | actual | gap |
|---|---|---|---|---|
| 0.00–0.40 | 279 | 0.368 | 0.405 | −0.037 |
| 0.40–0.45 | 790 | 0.429 | 0.454 | −0.025 |
| 0.45–0.48 | 1008 | 0.466 | 0.498 | −0.032 |
| 0.48–0.50 | 911 | 0.490 | 0.521 | −0.031 |
| 0.50–0.52 | 939 | 0.510 | 0.552 | −0.042 |
| 0.52–0.55 | 1287 | 0.534 | 0.556 | −0.022 |
| 0.55–0.60 | 998 | 0.570 | 0.595 | −0.025 |
| 0.60–1.00 | 244 | 0.631 | 0.656 | −0.025 |

The gap is roughly constant across every bucket — it does not grow at the
extremes. That is a **uniform shift**, which is what a missing intercept looks
like. A wrong `k` would show as a gap that widens away from 0.5, and it does
not.

The cause is visible in the margins: home teams win ~53% of games but the mean
predicted run margin is only about **+0.04 runs**. Runs and wins are not
linearly related around zero, and the sigmoid has nothing to absorb the
difference.

## Proposal (needs your OK)

Fit `p = sigmoid(a + k · margin)`, with `a` estimated **leave-one-season-out
inside the training window**, so the intercept never sees a game the model that
produced the margin had already seen. Measured that way, not assumed:

| season | fitted a | log loss now | with intercept | change |
|---|---|---|---|---|
| 2024 | +0.0745 | 0.685282 | 0.684303 | −0.000978 |
| 2025 | +0.0927 | 0.687824 | 0.685027 | −0.002797 |
| 2026 | +0.1075 | 0.689100 | 0.687945 | −0.001155 |
| **pooled** | | **0.687389** | **0.685740** | **−0.001649** |

Lower is better, so this is an improvement, and it is not small in context:
the model's entire current margin over its placeholder baseline is **+0.00375**.
An intercept is worth roughly **44% of that** on its own.

The fitted values are stable (+0.07 to +0.11) and all positive, which is the
consistency you would want before believing a correction rather than fitting
noise.

## Why I have not applied it

Three reasons, in order:

1. **The brief says to ask.** It changes the model, and every prediction the
   system makes.
2. **It would invalidate the paper-trading run in progress.** Every open paper
   bet was placed against the current mapping; changing it mid-flight mixes two
   models in one gate-2 sample.
3. **It is worth checking against a real market first.** The baseline here is
   still a placeholder. An intercept that helps against a home-rate constant is
   not guaranteed to help against a de-vigged closing line, and Phase 5 would
   answer that properly.

My recommendation: **do it, but after Phase 5's historical odds backfill**, so
the change is measured against a real market and does not disturb the paper
sample now running.

## If you want it now

`model/train.py:margin_to_win_prob` gains an `a` parameter, `walk_forward` fits
it leave-one-season-out exactly as it already does for `k_fit`, and
`model/persist.py` stores it in the bundle alongside `k`. `audit.py` should
then gain a calibration check so the gap above cannot drift back unnoticed.
