# Next task: stop trying to out-forecast the market; find where it's beatable

## Why the plan changed

Phase 5 answered the question the machine was built to ask. Against real de-vigged closing lines the MLB moneyline model loses in every season (pooled −0.0099 log loss, t = −5.5, n = 6,519), and it loses to the **opening** line almost as badly. NFL sides are worse (−0.037, t = −4.8). Read plainly: every feature the models use is already in the price. That's the normal result for public-data models on main markets, and the gates stopped us from paying to learn it.

So the goal changes. We're no longer trying to be a better forecaster of who wins than the market. We're testing three places where a small operation can be ahead of a book:

1. **Standing on the sharp line** — Pinnacle's de-vigged price is our "true" probability. A bet is +EV when a NJ book is off that line by more than its vig. Model skill isn't required. This works, and books limit accounts that do it, so it's a bonus and a control, not the product.
2. **Softer markets** — player props and first-5-innings lines, where the "line" is a book's derived number rather than a sharp consensus, limits are lower, and the books are slower. Our data fits two of these directly: MLB pitcher strikeouts (Statcast) and NFL receptions/receiving yards (nflverse target and snap shares).
3. **Information timing** — betting before the line moves on lineups, scratches, weather. The tables added in 3.5 are the raw material; the test is whether our number predicts *where the line moves*.

Realistic ceiling, stated up front: NJ books cap props at a few hundred to a couple thousand dollars and cut winners to tiny limits within weeks or months. This is hobby-scale money if it works at all. The durable value is a validated research process that can say "no edge" cheaply.

Same rules as before: CLAUDE.md applies, verify before claiming, back up first, one commit per step, **stop at every checkpoint for my OK**, plain English throughout.

**Credit budget:** about 58k credits remain this billing month and they don't carry over. Hard cap for this task: **55,000**. Two things compete for them: the props backtests (Part B) and confirmation seasons for the market-bias study (Part E). Both A1 and E are free and use data we already hold, so they run **first**, and the credit decision is made at the Part E checkpoint with those results in hand. Nothing is spent without my OK.

**Order of work:** Step 0 → A1 and Part E in parallel (free) → checkpoint → then B2/A2 costing and whatever the checkpoint decided.

---

## Step 0: Pre-register every experiment first

Create `docs/experiments.md` with one entry per experiment (A1, A2, B3, B4, C). Each entry is written **before any model is fit or price downloaded** and states:

- the question, the models compared, and the test seasons;
- the metric: pooled paired per-unit log loss against the de-vigged market, with a block-bootstrap CI (**by game** for props, by day for sides);
- **the pass rule:** better than the market by more than 2 SE pooled, better in every test season, survives leave-one-season-out. Anything less is "no edge found";
- **what won't be done afterward:** no adding or removing features, changing windows, or retuning after seeing test results. A failure gets recorded as a failure.

Commit this before Part A.

---

## Part A: Market-anchored tests on the existing models

These decide whether the current models carry *any* information the market lacks. If not, we stop spending time on them.

### A1. MLB moneyline (free)

Every season with an opening and closing no-vig price, walk-forward.

- **Baseline:** `p = p_open_novig`.
- **Disagreement:** logistic regression `logit(p) = logit(p_open) + b0 + b1·(logit(p_model) − logit(p_open))`, with `logit(p_open)` a fixed offset (coefficient held at 1). `p_model` includes the out-of-fold home intercept from docs-calibration.md.
- **Features:** same offset plus the 11 standardized features, ridge, alpha tuned on training seasons only.
- **Movement test:** regress `logit(p_close) − logit(p_open)` on `logit(p_model) − logit(p_open)`. A positive, significant slope means the model sees something the market later agrees with, which is an edge available at the open even though we lose at the close.

Report b1 with its CI (≈0: the disagreement is noise; <0: the market is right when we disagree), per-season and pooled log loss with CIs, and the leave-one-season-out result.

### A2. NFL sides, same tests (~1–2k credits; checkpoint before spending)

nflverse gives us closing moneylines but not openers. Dry-run the cost of one historical h2h snapshot per week, Tuesday ~noon ET, for 2020–2025 (about 18 weeks × 6 seasons × 10 credits). Then run the A1 tests on the NFL EPA model against the opener. NFL sharps' classic play is beating the opener; if the EPA model has any information, this is where it shows. **Stop for my OK before pulling.**

**Checkpoint: report both verdicts against the pre-registered rules.**

---

## Part E: Look behind the curtain — where is the market systematically wrong? (free)

"The market is efficient" is a summary, not a law. The Phase 5 result says our *model* can't beat the price. It doesn't say the price has no structural biases. This part tests specific, named hypotheses about how the market might be wrong, using the 6,519 games that already carry a real open and close. The rule is the same as everywhere else: name the bias and its direction **before** looking, and count anything else as exploration.

Two honesty rules apply to every result here:

- **Detectable is not bettable.** A bias has to beat the vig at a book I can actually use. At −110 the break-even is 52.4%, so a game the sharp line has at 50% needs a true probability above ~52.4% to be worth a bet at a NJ book, and ~51.2% at Pinnacle. Report every finding with the vig subtracted at the best NJ price, not just the raw gap.
- **Power is limited.** The standard error of a win rate is about 0.5/√n. With 2,500 games in a subgroup we can see a 2-point bias; a 1-point bias needs ~10,000. So the design is two-stage: **screen** on 2024–2026, and **confirm** on 2022–2023 (bought only for hypotheses that pass screening, roughly 28k credits, one shot, no re-tuning).

### E0. Pre-register (in the same docs/experiments.md)

For each hypothesis: the mechanism, the predicted direction, the exact test, the screening threshold (t > 2 in the predicted direction), and what "bettable" would require.

### E1. The market's recipe vs reality's recipe

This is the direct test of "the market overvalues starting pitchers and undervalues bullpens." Starters now average a little over five innings, down from six-plus fifteen years ago, and bullpens throw roughly 40% of innings. If the price still weights the starter like it's 2010, it shows up here.

First, measure the inputs properly, because 5-start K-BB% is a noisy results stat and a fair test needs a real starter-quality measure:

- **Starter quality:** projection-style blend of prior-season and current-season K-BB% and xwOBA-against, shrunk by batters faced, plus a Statcast process measure (average fastball velocity and its change vs the prior season), all as-of the game.
- **Bullpen quality:** 30-day K-BB% of relievers, weighted by how much high-leverage work each one gets.
- **Bullpen fatigue:** pitches thrown by the top three leverage arms in the prior two days, and whether each is on a third consecutive day.
- **Offense** and **park** as we have them.

Then two regressions on the same features:

1. `logit(p_close) ~ features` → the **market's weights**.
2. `outcome ~ features` (logistic) → **reality's weights**.

Compare coefficient by coefficient with CIs. "Overvalues starters" means the market's starter coefficient is larger than reality's; "undervalues bullpens" means the reverse on the pen terms. Then the residual version: `outcome ~ offset(logit p_close) + features`. Any coefficient that clears the threshold is something the price misses. (This replaces A1's "features" model; run it once, here.)

### E2. Subgroup calibration

Bucket games by starter-quality gap (ace vs replacement), bullpen-fatigue gap, and favorite size. For each bucket: actual home win rate vs market-implied, with a CI, and the EV at the best NJ price after vig.

### E3. Does public money push lines the wrong way?

For subgroups where the public has an opinion (marquee starters, big-market teams, heavy favorites, "hot" teams on a 5+ game streak), compare the log loss of the **open** vs the **close**. If the open is more accurate than the close in a subgroup, the movement was noise and the play is against the move at the close. Also report overall open-vs-close accuracy as the baseline.

### E4. A price-only map of where NJ books are off the sharp line

This is the highest-powered analysis available, because it needs no outcomes: prices aren't noisy. For every game, compute each NJ book's de-vigged probability minus Pinnacle's, and break it out by team (popular vs not), favorite size, starter fame, day of week, and hours to first pitch. Output a table of where DraftKings, FanDuel and BetMGM are systematically off the sharp line, and by how much relative to their own vig. Where the gap exceeds the vig, that's a bettable spot regardless of any model, and it feeds Part C directly.

### E5. Favorite-longshot bias

Do heavy favorites (−200 and beyond) win more often than implied, at Pinnacle and at each NJ book separately? Classic, well-documented, and mostly arbitraged away, but cheap to check and a good calibration of whether our tests can find known effects.

### E6. Early-season slowness

Is the market residual more predictable in April–May than in July–August, and does a Statcast process change (velocity down 1+ mph vs last year, new pitch mix) predict it? Projections are slow to update in spring; process data moves before results do.

**Checkpoint: report every hypothesis as pass/fail against its pre-registered threshold, with the vig-adjusted EV for anything that passes.** Then recommend how to spend the credits: confirmation seasons for a passing hypothesis, or the props backtests, or a split. I decide.

---

## Part B: Props program

### B1. Shared prop framework (no credits; start immediately)

Every prop is modeled the same way: **opportunity × rate**, with a count or skewed-continuous distribution on top. One codebase, one market-scoring path, and each new prop is a new target definition plus a market key.

- **Opportunity** (the info-driven half): batters faced for a pitcher; targets for a receiver; carries for a back. Comes from recent usage shrunk toward season and league priors, with game-script inputs the market already provides (spread and total are legal pre-game inputs).
- **Rate** (the skill half): K% per batter; catch rate and yards per target. Shrunk toward priors over ~300 events, adjusted for the opponent (as-of, shifted, never same-game data).
- **Distribution:** negative binomial for counts (strikeouts, receptions), gamma or lognormal for yards. Output is `P(over line)` for any posted line, with whole-number lines treated as over/push/under.
- **Leakage rules** as on the moneyline side. Props where the listed player didn't play (or the pitcher didn't start) are **dropped**, not scored, because the book voids them.
- **Validation before any prices exist:** walk-forward by month; calibration by decile; log loss vs a naive baseline (player's season average with a Poisson).
- **Correlation:** props within a game are correlated (a WR1's receptions vs a WR2's; QB pass yards vs his WRs), so bootstrap **by game**, and the bet engine takes at most one prop per team per market until correlation is modeled.
- Unit tests for the count model, push handling, and the void rule.

### B2. Preflight and costing (free or 1-credit calls; checkpoint)

Market keys, confirmed from the docs: MLB `pitcher_strikeouts` (also `pitcher_outs`, `pitcher_hits_allowed`, and `h2h_1st_5_innings`); NFL `player_receptions`, `player_reception_yds`, `player_rush_yds`, `player_pass_yds`. All come from the **event odds** endpoint: 1 credit per market per region live, **10** historical. Historical props exist from **2023-05-03**.

1. Using free or 1-credit calls, confirm which books post each market and **whether Pinnacle posts any of them**. Expect not. When Pinnacle is absent, "fair" = the de-vigged consensus across all US books (`regions=us`, which is one region regardless of book count, and gives a better consensus and finds the soft outlier). Record which fair source was used on every row.
2. Dry-run costs, including events-list calls, for:
   - **NFL** 2023–2025, `player_receptions` + `player_reception_yds` (+ `player_rush_yds` if it fits), one snapshot ~60 min before kickoff, plus a Thursday-noon opening snapshot for 2025 only;
   - **MLB** 2025, `pitcher_strikeouts`, one snapshot ~60 min before first pitch; and as a cheaper fallback, the second half of 2025 only.
3. Recommend an allocation under the 55k cap with NFL first (it's in season now; MLB can't paper-trade live until March).

**Stop for my OK. Nothing is spent before it.**

### B3. NFL receiving props: backtest, then live paper trading this season

- Build with nflverse: targets, receptions, air yards, snap shares, depth charts, injuries, and the schedule's spread/total, roof and wind. Rate × opportunity as in B1. Target redistribution when a WR1 is out is the single most plausible edge; make it an explicit feature (share of vacated targets).
- Match props to games with `feeds.odds_twin` and to players by nflverse `gsis_id` via name plus team; report match rates and unmatched names.
- Score against the fair line under the Step 0 rule. Also run the market-anchored version (offset = the market's over-probability plus b0 + b1·disagreement) and the **movement test** from the opener (2025 only). Break results out by market, by line size, and by book — descriptive only.
- **If it passes, go live in paper mode this season.** The weekly cadence fixes our worst operational problem: props post Tuesday–Thursday and kick off Sunday, so a local pull at 11:45am ET (1pm games) and 3:45pm (late games) lands inside the closing window at high coverage, and gate 2 fills in weeks, not months (~150 gradeable props a week). Gate 2 for props uses the same `info` metric (fair line moving toward our side) and the same coverage rule. Live pulls cost 1 credit per game per market, so estimate the monthly cost and tell me which plan it needs.

**Checkpoint after the backtest, before anything runs live.**

### B4. MLB pitcher strikeouts: backtest now, live next spring

Same framework: batters faced × K% per batter, with the opposing lineup's K% versus the pitcher's handedness and the park. Statcast has every input. Match by MLBAM pitcher ID. Score under the Step 0 rule. Also test `h2h_1st_5_innings` cheaply if credits allow, because our starter features matter more over five innings than nine, which is a known soft spot (dry-run first; it's a separate 10 credits per game).

**Checkpoint: report the verdict.** Live paper trading can't start until opening day, so the result mostly decides whether to keep collecting.

---

## Part C: Sharp-vs-soft line engine (a strategy of its own, and a control)

Build `bets/sharp_line.py`: for every game and market with a Pinnacle price, compute the fair probability and flag any NJ book whose price beats it by more than a threshold (start at 1.5% EV). Log flags as `mode='shop'` paper bets, graded on `ev_fair_close` exactly like the others, and **kept separate** from model bets in every report.

Three reasons it's worth building:
- it's the one strategy that's +EV by construction, so it's a sanity check: if it shows negative EV, the fair-line or grading code is broken;
- it isolates the "shop" component the gates already split out, so model results are never credited with shopping value;
- whatever the models turn out to do, it's a real (small, limited) edge you can act on.

It never stakes money on its own. Report expected pull frequency and credit cost for a live version; a paid plan at 100k credits allows roughly a pull every couple of minutes for one sport's main markets, which is the dry-run to show me.

---

## Part D: Housekeeping

1. **Home intercept (3.6):** measure against the real market close per season. Apply only if it helps in every season, and say whether it disturbs the paper sample.
2. **`docs/state-of-the-machine.md`:** one to two pages in plain English: what runs when and where, what each gate says and why, what's collected, what's open, and how to tell if something is broken. I'll use it for an independent review of the last ~30 commits.

## Not in scope

NBA (revisit in late October with the B1 framework: points, rebounds and assists via minutes projections and injury reports, which is the largest and best-documented props market but also the sharpest of the three), batter props, anytime-TD props (high vig), NHL, and anything that stakes money.
