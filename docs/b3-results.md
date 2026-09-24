# B3 results — NFL receiving props, 2023–2025

Run 2026-09-24 on the 814 games bought in B3. Pre-registered in
`docs/experiments.md`. Raw output: `docs/results/b3_backtest.txt`.

Reproduce (free, offline): `python research/b3/materialize.py` rebuilds
`data/props_nfl.parquet` from the purchased raw files, then
`python props/nfl_receiving.py` prints the backtest.

## Verdict: no edge found. The model loses to the price, and to a season average.

| market | fair price | n | model | market | margin | t |
|---|---|---|---|---|---|---|
| receptions | any | 72,164 | 0.692350 | **0.670232** | −0.0221 | **−11.2** |
| receptions | Pinnacle, same line | 52,051 | 0.696723 | **0.683249** | −0.0135 | **−7.4** |
| reception yards | any | 93,364 | 0.697758 | **0.675403** | −0.0224 | **−8.9** |
| reception yards | Pinnacle, same line | 33,321 | 0.703806 | **0.692466** | −0.0113 | **−5.4** |

Lower is better. The bar was a positive margin with t > 2 under **both**
fair-price definitions. The model is negative under all four, by five to eleven
standard errors.

**It also fails the cheaper bar that comes first.** Against a player's own
season-to-date average with a Poisson on top, the model is **−0.0378,
t = −11.0**. That is the B1 pre-price test, and it means the opportunity × rate
machinery adds nothing over "this receiver averages four catches".

Measured directly on the quantity rather than the over/under, the two are a
coin flip and the naive one is fractionally ahead:

| | model RMSE | naive RMSE | model corr | naive corr |
|---|---|---|---|---|
| receptions | 2.109 | **2.089** | 0.492 | **0.498** |
| reception yards | 28.97 | **28.92** | **0.519** | 0.516 |

## The pre-registered subgroup

B3's one named hypothesis was that the edge, if any, lives in **target
redistribution when a team-mate is ruled out**.

| market | fair | teammate OUT | nobody out |
|---|---|---|---|
| receptions | any | −0.0198 (t = −7.9) | −0.0235 (t = −8.4) |
| receptions | sharp | **−0.0092** (t = −3.5) | −0.0162 (t = −6.5) |
| yards | any | −0.0188 (t = −7.7) | −0.0244 (t = −6.6) |
| yards | sharp | **−0.0057** (t = −3.3) | −0.0147 (t = −4.8) |

The direction is right — the model is consistently **less bad** where a
team-mate is out, and on the sharp-line subset it is roughly half as bad. That
is weak evidence the mechanism is real. **It never gets above zero**, so under
the pre-registered rule it is a failure, and it is recorded as one.

## Two bugs found and fixed before this was reported

**The pre-registered feature was a column of zeros.** `vacated_share` was built
by joining the injury report onto the weekly player table and summing the
matched shares — but a player ruled out **has no row in that week's player
stats at all**, because nflverse only records men who played. The only rows it
could ever match were players listed out who then played anyway. The subgroup
test ran with one group in it and reported `n = 72,164` for "nobody out",
which is what gave it away. Fixed by taking each absent player's most recent
as-of share from before that week.

**The model was wildly over-confident.** Its raw deciles predicted 0.17 where
reality was 0.38, and 0.92 where reality was 0.55. The cause was estimating one
pooled `Var/mean` for the negative binomial, when that ratio grows with the
mean — so it fitted the crowded low-projection rows and left the high ones far
too sure. Replaced with a fitted variance function `Var = a·μ + b·μ²`, on
earlier months only.

**Even after that, the model gets a second, more generous scoring:** walk-forward
Platt scaling of its probabilities, fitted on strictly earlier months. A
monotone transform cannot invent information — it preserves the model's ranking
exactly — it can only stop bad confidence from masking good ranking. Raw log
loss 0.7138 became 0.6924. **It still loses to the market by 11 standard
errors**, so the loss is about information, not calibration.

## What the data itself says

Match rate **97.7%** of 169,480 prop rows (162,912 on full name, 2,616 on
surname-within-team-week where unique). The unmatched remainder is reported,
never dropped silently.

Props carry **much more vig than sides**: FanDuel 5.6%, Pinnacle 6.0%,
DraftKings 6.4%, BetRivers 7.1% — against 3.9–4.6% on moneylines. Break-even is
near **53%**, not 52.4%.

## Honest reading

The market's ranking is roughly twice as informative as the model's. Across the
model's own deciles the actual over-rate moves from 0.40 to 0.61; across the
market's it moves from 0.29 to 0.67. There is *some* signal in the model — the
ordering is positively related to outcomes — it is simply much less than what is
already in the price, and it is not additive to it.

This is the same answer Phase 5 gave for MLB moneylines, arrived at
independently on a market that was supposed to be softer. The one encouraging
thread is the inactive-team-mate subgroup, which is where the model is least
beaten and where the mechanism is most plausible. It is not a finding. If it is
ever pursued it needs its own pre-registration and its own data.
