# Pre-registered experiments

**Written 2026-09-23, before any model in it was fit and before any price in it
was downloaded.** That is the whole point of the file. Anything added later is
dated and labelled as such, and anything found that is not written here is
**exploration**, not a result.

Why this exists: Phase 5 showed the MLB moneyline model loses to the real
de-vigged close in all three test seasons (pooled −0.0099 log loss, t = −5.5,
n = 6,519) and loses to the morning price almost as badly. NFL sides are worse
(−0.037, t = −4.8). Read plainly, every feature these models use is already in
the price. The plan now is to test specific named places where a small
operation might still be ahead of a book. The failure mode we are guarding
against is obvious: run enough tests on 6,519 games and something clears t > 2
by luck. Writing the direction down first is what separates a finding from a
coincidence.

---

## Common rules (apply to every entry unless it says otherwise)

**Metric.** Pooled paired per-game log loss against the de-vigged market
probability. Paired, because the question is always "does A beat B *on the same
games*", and the per-game difference has far less variance than the difference
of two averages.

**Uncertainty.** Block bootstrap, 10,000 resamples, blocked **by day** for
sides (all games on one date move together — weather, a national narrative, a
bad number that every book copies) and **by game** for props (two receivers in
the same game are correlated). Report the 95% interval, not just a t.

**Pass rule.** All three, or it is "no edge found":

1. better than the market by **more than 2 pooled standard errors**;
2. better in **every** test season, not on average;
3. survives **leave-one-season-out** — drop each season in turn, and the
   remaining pooled result still beats the market.

**What will not happen after seeing a test result.** No adding or removing
features, no changing window lengths, no retuning hyperparameters, no swapping
the metric, no dropping a season that "had something weird going on". A failure
is recorded as a failure in this file, with the number. If a genuinely new idea
comes out of a failed test, it gets a new pre-registered entry and a fresh test
set — it does not get scored on the data that suggested it.

**Alpha and the multiplicity problem.** Part E runs six hypotheses across
several subgroups each. A t > 2 screen on that many tests will produce false
positives by construction; that is *expected and accepted*, because screening
is deliberately generous. The protection is not a Bonferroni correction, it is
the **two-stage design**: nothing is believed until it repeats out of sample on
2022–2023, which has to be bought. Any hypothesis reported as "passed
screening" is explicitly **not yet a finding**.

**Detectable is not bettable.** Every result is reported twice: the raw gap,
and the gap after the vig at the best price actually available to us. At −110
the break-even is 52.38%. A 1-point calibration error on a coin-flip game is
real and worth nothing.

---

## What the data actually is (verified 2026-09-23, before any test)

Stating this here because two entries below are weaker than the brief assumed,
and it is better to say so now than to discover it while reporting.

| thing | what we have |
|---|---|
| seasons with a real de-vigged market | **2024, 2025, 2026** (6,519 games with a model prediction) |
| seasons without one | 2022, 2023 — the market column is a home-rate placeholder. Confirmation requires buying these. |
| books | **four**: Pinnacle, DraftKings, FanDuel, BetMGM. "NJ books" below means DK / FD / MGM. |
| "close" | latest snapshot before first pitch; median **45 min** out, Pinnacle on 7,020 of 7,032 games |
| "open" | the **10:00 ET game-day morning** snapshot — median **8.8 hours** before first pitch, not a true opener (a true opener posts days earlier, and we never captured it) |
| Statcast | full pitch-level, 2022–2026, **3.55 M pitches with all 119 columns** available offline in the local pybaseball cache, including release speed, pitch type, pitcher handedness and xwOBA. The trimmed 13-column extract on disk does not have these; the enriched one is rebuilt from cache at no cost. |

**Consequence for A1 and E3:** wherever this file says "open" it means the
game-day morning price. The movement being tested is morning → close, which is
a real and substantial window (8.8 hours, and the lineup/weather news lands
inside it), but it is **not** the classic "beat the opener" test, and it will
not be reported as one.

---

## Part A — does either existing model carry information the market lacks?

### A1. MLB moneyline, market-anchored (free, runs now)

**Question.** The model loses to the close outright. Does it nevertheless carry
*any* information the price does not already have?

**Data.** 2024–2026, every MLB game with a de-vigged morning and closing price
and an out-of-sample model prediction. Walk-forward exactly as the model is
trained: never fit on a season it is scored on.

**Models compared.**

- **Baseline:** `p = p_open_novig`.
- **Disagreement:** `logit(p) = logit(p_open) + b0 + b1 · (logit(p_model) − logit(p_open))`,
  with `logit(p_open)` entered as a **fixed offset** (coefficient pinned at 1,
  not estimated). `p_model` includes the out-of-fold home intercept from
  `docs/calibration-3.6.md`.
- **Features:** the same offset plus the 11 standardized features, ridge, alpha
  tuned on training seasons only. *(Superseded — see E1. The feature-level
  version of this question is answered once, properly, in E1's residual
  regression, with real starter and bullpen measures instead of the current
  noisy ones. Running it twice would be two shots at the same target.)*
- **Movement test:** regress `logit(p_close) − logit(p_open)` on
  `logit(p_model) − logit(p_open)`.

**Predicted direction, written before fitting.** `b1 ≈ 0`, or negative. The
Phase 5 result makes b1 > 0 unlikely: if the model's disagreements with the
price were informative, it would not be losing by 5.5 SE. The honest prior is
that b1 lands near zero with a tight interval, and the value of the test is the
**interval**, not the point — a tight interval around zero is a real answer
("there is nothing here"), where a wide one would only mean the test was
underpowered.

**Movement slope:** predicted ≈ 0. A positive, significant slope would mean the
model sees something the market later agrees with, i.e. an edge available in
the morning that is gone by the close. This is the one result in A1 that would
change what we do, so it is the one to be most suspicious of.

**Pass rule.** As above. For the movement test specifically: slope > 0 with
t > 2 pooled *and* positive in each of the three seasons, *and* the implied
morning-price EV clears the vig at a NJ book. A slope that is positive but
worth less than the vig is reported as "real, not bettable".

**Reported:** b1 with CI, per-season and pooled log loss with CIs,
leave-one-season-out, movement slope with CI.

### A2. NFL sides, same tests (~1–2k credits — NOT AUTHORIZED, checkpoint first)

**Question.** Same as A1, for the NFL EPA model. NFL is the one market where
beating the *opener* is the classic sharp play, and nflverse gives us closing
moneylines but no openers, so an opener has to be bought.

**Cost.** One historical h2h snapshot per week, Tuesday ~noon ET, 2020–2025:
about 18 weeks × 6 seasons × 10 credits ≈ **1,080 credits**, plus events-list
calls. To be dry-run and confirmed before anything is pulled.

**Predicted direction.** Same as A1: b1 ≈ 0. NFL sides lost by more than MLB
(−0.037), so the prior here is worse, not better.

**Status: not started. Nothing is pulled without an explicit OK.**

---

## Part E — where is the market itself wrong? (free, runs now)

"The market is efficient" is a summary, not a law. Phase 5 said our *model*
cannot beat the price; it said nothing about whether the price has structural
biases. Each hypothesis below names a mechanism and a **direction** before
anything is looked at.

**Two-stage design.** Screen on **2024–2026** (6,519 games, real market).
Confirm on **2022–2023**, which must be bought (~28k credits), **one shot, no
re-tuning**, and only for hypotheses that pass screening. A hypothesis that
passes screening and fails confirmation is dead, and gets written up as dead.

**Power, stated up front.** The standard error of a win rate is about 0.5/√n.
With 2,500 games in a subgroup we can see a 2-point bias; a 1-point bias needs
about 10,000 games and we will never have it. So E2, E3 and E5 can only find
**large** effects, and a null from them means "no large effect", not "no
effect". E4 is exempt from this: it compares prices to prices, and prices are
not noisy.

**Screening threshold for every hypothesis: t > 2 in the pre-registered
direction.** Wrong-signed results, however large, are failures — not
"interesting reversals".

### E1. The market's recipe vs reality's recipe

**Mechanism.** Starters now average a little over five innings, down from
six-plus fifteen years ago, and bullpens throw roughly 40% of innings. If the
price still weights the starting pitcher the way it did in 2010, the market's
coefficient on starter quality will be larger than reality's, and its
coefficient on bullpen quality smaller.

**Inputs must be built properly first.** 5-start K-BB% is a noisy results stat
and would fail this test through measurement error alone, which would be
indistinguishable from "no bias". Before any regression is run:

- **Starter quality:** projection-style blend of prior-season and
  current-season K-BB% and xwOBA-against, shrunk toward the league mean by
  batters faced, plus a process measure from Statcast (average four-seam
  velocity and its change against the prior season). All as-of the game, all
  `shift(1)`-ed at source.
- **Bullpen quality:** 30-day K-BB% of relievers, weighted by each reliever's
  share of high-leverage work.
- **Bullpen fatigue:** pitches thrown by the top three leverage arms in the
  prior two days, and whether each is on a third consecutive day.
- **Offense** and **park** as currently built.

**Test.** Two regressions on the *same* feature matrix:

1. `logit(p_close) ~ features` → the **market's** weights;
2. `outcome ~ features`, logistic → **reality's** weights.

Compare coefficient by coefficient with CIs. Then the decisive one:
`outcome ~ offset(logit p_close) + features` — any coefficient clearing the
threshold there is information the closing price does not contain.

**Predicted direction (the actual pre-registration).**

| coefficient | predicted |
|---|---|
| starter quality, market vs reality | **market larger** (overvalued) |
| bullpen quality, market vs reality | **market smaller** (undervalued) |
| bullpen fatigue, market vs reality | **market smaller** (undervalued) |
| residual regression, starter terms | **negative** (fading the starter edge is +EV) |
| residual regression, bullpen terms | **positive** |

**Bettable requires:** a residual coefficient large enough that the top decile
of its fitted residual implies a probability more than 2.4 points from the
close at −110, after vig, at a NJ book.

### E2. Subgroup calibration

**Mechanism.** If E1's bias is real it should show as miscalibration in the
subgroups where the relevant gap is largest.

**Test.** Bucket by starter-quality gap (ace vs replacement), bullpen-fatigue
gap, and favorite size. Per bucket: actual home win rate vs market-implied,
with CI, and EV at the best NJ price after vig.

**Predicted direction.** Home teams with a **large starter-quality edge** are
**overpriced** (actual win rate below implied); teams facing a **tired**
bullpen are **underpriced**. Favorite-size buckets are E5's job, reported here
without a direction.

### E3. Does public money push lines the wrong way?

**Mechanism.** Where the public has a loud opinion, line movement can be driven
by money rather than information, and the later price can be *worse* than the
earlier one.

**Test.** For subgroups with a public opinion — marquee starters, big-market
teams, heavy favorites, teams on a 5+ game winning streak — compare the log
loss of the **morning** price against the **close**. Also report overall
morning-vs-close accuracy as the baseline.

**Predicted direction.** Overall, the **close is more accurate** than the
morning price (this is close to a law, and a failure here means the pipeline is
broken, so it doubles as a sanity check). Within public subgroups, the gap
**narrows**, and the pre-registered hypothesis is that in at least one such
subgroup the **morning price is more accurate** — i.e. the move was noise and
the play is against it at the close.

**Bettable requires:** the reversal to exceed the vig, measured as EV from
betting against the move at the closing NJ price.

### E4. A price-only map of where NJ books sit off the sharp line

**Mechanism.** DraftKings, FanDuel and BetMGM shade prices toward the public
side and toward their own risk; Pinnacle does not. This needs **no outcomes**,
so it is by far the highest-powered thing available here.

**Test.** For every game and every NJ book, de-vigged probability minus
Pinnacle's, broken out by team (popular vs not), favorite size, starter fame,
day of week, and hours to first pitch. Output a table per book.

**Predicted direction.** Each NJ book is systematically **short on popular
teams** (Yankees, Dodgers, Red Sox, Cubs, Braves, Mets, Phillies) — i.e. it
prices them higher than Pinnacle does — and the gap is **larger on weekends and
larger further from first pitch**, when recreational money dominates and the
book has not yet been arbitraged.

**Bettable requires:** gap > that book's own vig on the game. Where it is, that
is a bet with no model at all, and it feeds Part C directly.

### E5. Favorite–longshot bias

**Mechanism.** The classic one: bettors overpay for longshots, so heavy
favorites are underpriced. Well documented in most betting markets, mostly
arbitraged out of major ones.

**Test.** Do heavy favorites (−200 and beyond) win more often than implied, at
Pinnacle and at each NJ book separately?

**Predicted direction.** Heavy favorites win **more** often than implied.
Effect predicted to be **near zero at Pinnacle** and **small but positive at
the NJ books**.

**Why it is here even though it is probably dead:** it is a known effect with a
known sign, so it calibrates whether these tests can find anything at all. If
E5 finds nothing anywhere, that is evidence about our power, not just about the
market. Expected n at −200+ is small; the power note above applies hardest
here.

### E6. Early-season slowness

**Mechanism.** Projections lean on prior-season results and are slow to update
in April and May. Statcast process data — velocity, pitch mix — moves before
results do. A pitcher throwing 2 mph slower than last year is a different
pitcher before his ERA says so.

**Test.** Is the market residual (`outcome − p_close`) more predictable in
April–May than in July–August? Does a process change — four-seam velocity down
1+ mph against the prior season, or a materially changed pitch mix — predict
the residual?

**Predicted direction.** Residual predictability is **higher in April–May**;
a velocity **drop** predicts that pitcher's team **underperforming** its
closing price.

**Bettable requires:** the April–May effect to clear the vig on its own, in
that window only. A seasonal effect that only works for six weeks a year is
still a real edge, but the bet count is small and that will be stated.

---

## Part B — props

### B1. Shared framework (no credits)

Not a hypothesis; the machinery every prop test runs on. **Opportunity × rate**,
with a count or skewed-continuous distribution on top. Negative binomial for
counts, gamma or lognormal for yards. Props where the listed player did not play
are **dropped, not scored**, because the book voids them. Bootstrap **by game**.
At most one prop per team per market until correlation is modeled.

### B2. Preflight and costing (free / 1-credit calls — checkpoint before B3/B4)

Confirm which books post each market and whether Pinnacle posts any of them
(expected: no — when Pinnacle is absent, "fair" is the de-vigged consensus
across all US books, recorded per row). Dry-run costs. **Nothing spent without
an OK.**

### B3. NFL receiving props

**Question.** Does an opportunity × rate model beat the de-vigged consensus on
`player_receptions` and `player_reception_yds`?

**Predicted direction, written now.** The single most plausible edge is
**target redistribution when a WR1 is out** — the share of vacated targets is
an explicit feature, and the pre-registered claim is that the model beats the
market **specifically on games where a team's top target is inactive**, and is
at best neutral elsewhere. Props are softer than sides, so unlike A1 the prior
here is genuinely uncertain rather than pessimistic.

**Pass rule:** the common rule, bootstrapped by game, plus the
market-anchored and movement versions. Break out by market, line size and book
— **descriptive only**, never as a filter chosen after the fact.

### B4. MLB pitcher strikeouts

**Question.** Does batters-faced × K%-per-batter, with the opposing lineup's K%
versus the pitcher's handedness and the park, beat the de-vigged consensus on
`pitcher_strikeouts`?

**Predicted direction.** The model beats the market on the **opportunity** half
(batters faced is game-script driven and books are slow on it) and not on the
rate half. Also flagged now, before testing: `h2h_1st_5_innings` is where our
starter features should matter most and the nine-inning line least — a separate
10 credits per game, dry-run first.

---

## Part C — sharp-vs-soft line engine

Not a hypothesis. `bets/sharp_line.py` flags any NJ book whose de-vigged price
beats Pinnacle's by more than a threshold (start at 1.5% EV), logged as
`mode='shop'` paper bets, graded on `ev_fair_close`, **kept separate from model
bets in every report**.

**It is +EV by construction, which makes it a test of our code rather than of
the market.** If it shows negative EV, the fair-line or grading path is broken
and every other number in this file is suspect. That is its main job. It never
stakes money on its own.

---

## F1 — vacated targets, pre-registered 2026-09-24, BLIND TO 2026 DATA

**Registered before a single 2026 prop price had been collected, let alone
looked at.** That is the whole point of writing it now: B3 found that the one
place its model was least beaten was games where a team-mate had been ruled
out, and scoring that idea on the 2023-25 sample that suggested it would be
fitting the same data twice. So the question gets a fresh season, recorded in
advance, and the season is being collected from today.

**Question.** In games where a team's leading receiver by **prior-four-week
target share** is ruled OUT before the pull, does the prop framework's number
beat the fair close on that team's **remaining** receivers?

**Population.** 2026 NFL regular season, from week 4 onward (collection starts
2026-09-24). A team-game qualifies when the player with the highest
prior-four-week target share on that team appears on the pre-pull injury report
with status `Out`. Scored rows are the *other* receivers on that team with a
posted line.

**Data.** `props/collect.py` pulls `player_receptions` and
`player_reception_yds` once per game near kickoff, with Pinnacle named, plus a
timestamped injury snapshot at each pull. The injury status used is the one
**recorded at pull time**, never a later correction — B3's feature died because
a player ruled out has no row in the week's player stats, so the status was
being reconstructed after the fact from a table that could not contain him.

**Metric and pass rule.** Unchanged from the common rules: pooled paired
per-prop log loss against the de-vigged fair close, bootstrapped **by game**,
better by more than 2 SE, and positive in **both** fair-price definitions
(Pinnacle at the same line; consensus fallback). Also reported on the
complement — the same teams' receivers in weeks when nobody was out — because
a result that appears in both groups is not about vacated targets.

**Predicted direction.** Positive, but small. B3's measurement was
−0.0092 against −0.0162 on the sharp subset: less bad, never good. The honest
prior is that this closes part of the gap and not all of it, and the most
likely single outcome remains "no edge found".

**Two fixes that must be made BEFORE the test, and cannot be tuned on 2026
data.** Both are recorded here so that making them later cannot be mistaken for
tuning:

1. **Negative-binomial dispersion.** One pooled `Var/mean` is wrong: for a
   negative binomial that ratio grows with the mean, so a single number fits
   the crowded low-projection rows and leaves the high ones far too confident.
   The fitted variance function `Var = a·μ + b·μ²` is already in
   `props/nfl_receiving.py:calibrate_walk_forward` and must be used.
2. **The `vacated_share` join.** Must come from each absent player's most
   recent as-of share, taken from before that week — never from a join onto the
   week's player table, which by construction contains only players who played.

**What will not happen afterwards.** No feature added, no window changed, no
threshold moved, no switch of fair-price definition after seeing the result.
The four-week lookback and the `Out` status are fixed here and now.

---

## Scanner brief (2026-09-25) — pre-registered before any strategy exists

**Written 2026-09-25, during Phase A of `docs/briefs/2026-09-25-next-task.md`,
before a single scanner price was collected and before any strategy was
written.** The brief changes the machine's job from predicting games to finding
prices that are wrong. Every strategy it adds (B, E1–E5) gets its own entry
here before its first result is looked at. This section fixes what they all
share, so no later entry can loosen it quietly.

### S0. Rules every strategy inherits

**Its own record, nobody else's.** Each strategy has a block in
`validation.json` under its own name, the same shape as a sport's. No strategy
counts another's positions, and a pass by one opens nothing for another.

**Gate 1 — its own backtest.** The strategy's entry here names one historical
test, its pass rule, and what will not be tuned. `scanner.gates.record_backtest`
refuses a strategy whose experiment id is not a heading in this file.

**Gate 2 — its own paper record.** Exactly the rules sport models are held to
(`model.validation.record_paper`, unchanged): 50+ graded positions; the metric's
mean clears zero by `PAPER_CLV_SIGMA` = 3 standard errors; coverage (graded ÷
settled) at least 0.75; no ET slot over 60% of graded positions unless coverage
is at least 0.90 (a position's slot is its event's start time, or its first
fill's time when the market has no start time); and a placebo, run through the
same pipeline under the same strategy name, that must **not** clear the same bar.

**The metric is one of two, chosen in the strategy's own entry:**

- `info` = fair close ÷ fair at entry − 1, both from `scanner.fair.fair_value`.
  **Both ends must come from the same source** (Pinnacle and Pinnacle, or
  consensus and consensus). A position whose ends disagree is settled but not
  graded, so it counts against coverage. (The 2026-09-24 review found the MLB
  gate mixing the two on every graded bet; strategies start without that flaw.)
- `realized_ev` = profit ÷ capital staked, after fees, per position, where
  `info` has no meaning (no fair close exists).

**Looks.** Gate 2 is re-tested at most **twice per ET day** per strategy, and
every look is written to the `gate_looks` table. That is the cadence the
SEQUENTIAL TESTING simulation in `audit.py` covers; a faster cadence would turn
the 3-SE bar into a looser one without anyone changing a number.

**Gate 3.** A person calls `arm("<strategy>")`. It refuses unless gates 1 and 2
pass. Nothing automated calls it.

**Shared machinery, not tunable after a result.** Fair value comes only from
`fair_value`: Pinnacle de-vigged at the latest pull at or before the moment →
else the mean de-vigged price across books at that pull → else the venue's own
mid; the source is recorded. Every EV is after fees, and the fee model is
recorded on the price row. A paper fill uses the **next** observed price strictly
after the order, never the current one, and never more than the size shown.
*Added 2026-09-25, during Phase A, before any strategy existed:* nor a price
captured at or after the game's start. A-V4's run showed pregame orders
reaching in-play prices; `fair_value` already refuses those, so a fill there
would be graded against a price from a different market state.
None of these, the gate thresholds, the coverage rule or a strategy's placebo is
changed after seeing any strategy's result. A fee model changes only to match a
venue's published schedule, with the date.

**Amendments from the Phase A review.** *Added 2026-09-25 by the Phase A
review, before any strategy existed.* The review found gaps in S0 as written.
Every change below tightens a rule. None loosens one, and no threshold moves.
Each one names the code that enforces it.

- **Gate 1 is the strategy's own entry.** "A heading in this file" was not
  enough: any heading counted, even "Results log", and nothing tied the
  experiment to the strategy. `scanner.gates.record_backtest` now refuses
  unless the experiment is the one the strategy registered against. It must be
  a whole markdown heading above the Results log, outside code fences. No two
  strategies may register against the same entry: `scanner.strategies.register`
  refuses the second. A name whose gate record already holds a different
  experiment is refused (`model.validation.record_backtest`). A new definition
  is a new strategy under a new name, with none of the old one's positions,
  looks or gate-2 record. S0 itself, and the other section headings in this
  file, are not entries. The code cannot tell a section heading from an entry,
  so that rule rests on whoever writes the strategy.
- **Gate 2 counts only positions that are graded AND settled.** For `info`,
  coverage is positions graded and settled ÷ positions settled. A position
  graded at its game's start but not yet settled counts in neither. For
  `realized_ev`, coverage is positions settled among those due ÷ positions
  due. "Due" means the market resolved more than 36 hours ago, or the position
  has no `resolves_at` at all, in which case it counts against coverage until
  it settles. The top and the bottom count the same positions
  (`scanner.gates.measured`).
- **The placebo must place.** A placebo that must "not clear the same bar"
  blocks nothing if it never places. So gate 2 also fails unless the placebo
  has at least 50 graded, settled positions of its own. That means Phase B's
  settlement must settle placebo positions too, or no strategy can pass.
- **`realized_ev` cannot pass gate 2 yet.** The 3-SE bar was simulated on
  near-normal values, the kind `info` produces. `realized_ev` is win-or-lose
  instead. On it, a strategy with no skill that buys favourites at their fair
  price clears the bar 5.5% of the time at 80¢, 14.7% at 95¢ and 40.6% at 98¢
  (120 days, 50 graded a day, two looks a day, 4,000 trials). The same volume
  on `info` gives 2.3%. So a `realized_ev` strategy's evidence is recorded and
  its gate 2 fails (`scanner.gates.UNCALIBRATED`), until a bar simulated for
  that payoff is pre-registered here.
- **A look is claimed before it is taken.** Each gate-2 look is written to
  `gate_looks` and committed, under a write lock, before gate 2 is evaluated.
  A crash, a rollback, a busy database or a second scorer running at the same
  moment can cost a look but never add one.
- **Fills.**
  - A size shown is filled once per strategy and mode, with paper and
    placebo kept apart. Our fills never leave the recorded book. So an offer
    still showing at the same price in a later observation is the same offer,
    and what this strategy already took from it is gone, until an observation
    shows a worse price and not that one. This can under-fill, never
    over-fill.
  - A maker whose limit is at or through the ask showing when it is placed
    is refused: it would take, not rest.
  - No order is accepted at or after the game's start, or at or after the
    market's `resolves_at`.
  - A taker order cannot carry an `expires_at`. It never rests, so it never
    honoured one.
  - An order's fees, exposure and EV are priced on the fee model showing at
    the order's moment (the market's latest price at or before it), not on a
    schedule first seen later.
  - Kalshi fees are rounded once per order, as Kalshi's published Fee
    Rounding rule does (see [venues.md](venues.md)). Once a fill is in, the
    order has paid its whole cash so far rounded up to the cent once. The
    final sub-cent rebate is still ignored, so this stays conservative.
  - A fill that would take a position's stake past the exposure its daily
    cap counted is not made.
- **Grading waits for the start.** A position is graded only once its game
  has started. Before that, the newest pull is only the latest price so far,
  and a graded position is never graded again.
- **"The start" is one start per game.** Everywhere above, and in
  `fair_value`'s in-play cutoff, the start is `scanner.fair.game_start()`:
  the one start the books give the game, capped by an exchange contract's own
  start when that is earlier. It is never a market's own `event_start` alone.
  The books' start is judged pull by pull for the whole game by
  `scanner.venues.sportsbook.next_start`. An earlier start always wins. A later
  one counts only if it was reported before it passed.

### A-V. Phase A verification checks, pass rules fixed before running

These check code, not the market. None of them computes a strategy's profit or
EV on real data; the first such number belongs to Phase B or E and needs its own
entry above.

- **A-V1. One fair price, two code paths.** Copy every archived moneyline pull
  (`odds_snapshots`) from the last seven days of the 2026-09-25 00:45 ET copy of
  production into the new `prices` table, then ask `fair_value` for both sides of
  every pull. **Pass:** every value equals `bets/log.fair_prob` on the same pull
  within 1e-12, with the same source label. Any disagreement is a defect in one of
  them, fixed before Phase A is reported.
  *Clarification, added 2026-09-25 after `fair_value` was written and before
  A-V1 was run:* `fair_value` refuses any price captured at or after the game's
  start (in-play), which `fair_prob` does not check because its callers only
  ever pass pregame times. In-play pulls in the sample are counted and reported
  as refusals, not scored as agreements or disagreements.
  *Clarification, added 2026-09-25 by the Phase A review, after A-V1 was first
  run:* `audit.py` now enforces that rule, and it decides "in play" the way
  `fair_value` does. It works out each game's start again from the raw pulls,
  pull by pull, by `scanner.venues.sportsbook.next_start`'s rule: an earlier
  reported start always wins, and a later one counts only if it was reported
  before it passed. A pull captured at or after that start is in play and
  must be refused. A value priced from any price captured at or after the
  start fails A-V1. One case is neither kind. A pull taken before that start,
  but after the start its own report gave, is pregame to `fair_value` and in
  play to `fair_prob`, which judges a pull by its own report alone. That
  happens when the feed announced a delay only after the old time had passed
  but before the new one. Such a pull is counted as "delayed-start, not
  scored". Replayed over the whole archive (83,294 pulls, 2024-03-28 to
  2026-09-25; each pull gives two values, home and away): 141,638 agree,
  0 disagree, 24,942 of 24,942 in-play values refused. There are 8
  delayed-start values, in 4 games: mlb-979a1e9d 2024-04-02, mlb-e841062c
  2025-05-13, mlb-bdf1f383 2025-06-27 and mlb-8ea201bd 2026-07-05. The same
  replay of the pre-review code, under the audit's old rule, gave 141,614
  agree and 48 disagree, and it priced 34 in-play pulls.
- **A-V2. Kalshi's fee arithmetic.** **Pass:** a 50¢ taker contract carries a
  model fee of $0.0175; 100 of them $1.75; a maker on a plain `quadratic` series
  pays nothing; an unread fee type (`flat`) refuses rather than guessing.
- **A-V3. The polling budget.** **Pass:** the planner's own projection keeps the
  whole brief at or under 6,000 credits, and normal operation at or under 15,000 a
  month including the props collector's 3,000 cap.
- **A-V4. No look-ahead.** Orders placed at real capture times against real
  archived price sequences. **Pass:** every simulated fill uses a price captured
  strictly after the order; zero exceptions. Timestamps only — no P&L is
  computed.

---

## Results log

Part E is written up in full in [part-e-results.md](part-e-results.md);
raw output in `docs/results/part_e.txt`.

Filled in as each experiment completes. Failures stay in the file.

| experiment | run on | verdict | number |
|---|---|---|---|
| A1 | 2026-09-23 | **FAIL** (real signal, ~12x too small to bet) | see below |
| E1 | 2026-09-23 | **FAIL** | best residual term t = -1.31; 2 of 4 starter/pen terms wrong-signed |
| E2 | 2026-09-23 | **FAIL** | no bucket clears t = 2 in the predicted direction |
| E3 | 2026-09-23 | **FAIL** | every subgroup favours the close; big-market t = +2.31 the wrong way |
| E4 | 2026-09-23 | **FAIL** (all 3) | popular teams priced LOWER, t = -7.4..-9.5; no weekend or time pattern |
| E5 | 2026-09-23 | **FAIL** | +2.73 pts at Pinnacle, t = +1.46; right direction, underpowered |
| B3 | 2026-09-24 | **FAIL** | loses to the market by 5-11 SE and to a player season average by 11 |
| E6 | 2026-09-23 | **FAIL** | April-May +0.0028 vs July-Aug +0.0024; velocity wrong-signed early |
| A-V1 | 2026-09-25 | **PASS**, after one fix | 2,012 of 2,012 pregame values equal (largest difference 0.0), 354 in-play refused. First run: 10 disagreements — a conversion bug on rain-delayed games, fixed. *Rerun 2026-09-25 after the Phase A review, under the stricter rule above:* the same copy gives 2,012 agree, 0 disagree, 354 of 354 in-play refused, 0 delayed-start (1,183 pulls since 2026-09-18). The whole archive gives 141,638 / 0 / 24,942 of 24,942, with 8 delayed-start values in 4 games. The week ending 2026-06-06 12:00 UTC failed on the pre-review code (1,794 agree, 4 disagree) and now passes (1,798 agree, 0 disagree, 286 of 286 in-play refused) |
| A-V2 | 2026-09-25 | **PASS** | 0.0175 / 1.75 / maker 0 / `flat` refuses |
| A-V3 | 2026-09-25 | **PASS** | brief ~4,410 of 6,000; normal ~10,319 + 3,000 props of 15,000. *Corrected 2026-09-25 by the Phase A review:* those were the planner's figures for one level held all day at a flat pace. The loop re-picks its level every 5 minutes and rolls unspent credits forward, so it spends up to its caps: 6,000 of 6,000 over the brief, and 12,000 a month + 3,000 props = 15,000 of 15,000. `poll --plan` now prints these. The pre-registered rule is "at or under", so it still passes |
| A-V4 | 2026-09-25 | **PASS**, after one fix | 0 of 3,629 fills at or before the order. It also found 617 fills at in-play prices; nothing fills after the start now (3,012 fills, 0 in play) |

### A1, run 2026-09-23 on 6,513 games (2024-2026)

**Verdict: no edge found.** Two of the three tests came out as predicted; the
third did not, and then failed the bettable clause by a factor of about twelve.

| test | predicted | measured | |
|---|---|---|---|
| b1, weight on our disagreement | 0, or negative | **+0.103**, CI [-0.073, +0.273], t = +1.20 | as predicted (indistinguishable from 0) |
| anchored log loss vs the morning price | no improvement | **-0.00044**, CI [-0.00100, +0.00012], t = -1.52 | as predicted (no improvement) |
| movement slope | 0 | **+0.0241**, CI [+0.0164, +0.0312], t = +6.48 | **NOT as predicted** |

b1's per-season values (-0.005, +0.224, +0.061) are all over the place, which
is what a coefficient with no signal behind it looks like.

**The movement slope is real.** It survived both artifact checks:

- *Shared denominator.* `logit(p_morning)` sits on both sides of the
  regression, so morning-price measurement error manufactures a positive slope
  for free. Measuring the morning price twice from independent sources
  (Pinnacle vs the de-vigged NJ consensus) and using the unbiased
  two-measurement estimator takes +0.0284 down to **+0.0241** - so the artifact
  was real but small, about 15%. *(The obvious fix of simply swapping one
  measurement for the other is itself biased, downward, and gave +0.0096. Both
  naive versions straddle the right answer; the estimator in
  `research/a1_movement.py` uses both measurements in both places.)*
- *Late starters.* The model knows who actually started; the 10:00 price
  sometimes does not. Refitting without the two starter features leaves
  **+0.0104, t = +3.03** - smaller, but alive, and the stripped model disagrees
  with the market MORE (sd 0.310 vs 0.277), so the drop is not a power
  artifact. Trimming the games where the market moved most leaves +0.0126 at
  the 90th percentile, t = +5.03: broad-based, not a handful of scratches.

So the model does see something the market has not finished pricing at 10 a.m.,
and roughly half of it is about starting pitchers - which is E1's hypothesis
arriving early, by a different route.

**It is not bettable, and it is not close.** A one-standard-deviation
disagreement predicts the close moving 0.0068 logit toward us: **0.17
percentage points** of probability. The NJ books' morning overround is
3.9-4.6%, so a bet needs to be worth about **2 points** to break even. The
signal is roughly **twelve times too small**.

Betting it directly confirms that. Top decile of disagreement, at the best NJ
morning price, vig paid: **633 bets, 34.3% won against a 37.5% break-even, ROI
-9.5%** (sd of the mean 5.1).

**Nothing was tuned in response to any of this.** The b1 and log-loss tests
stand as pre-registered failures; the movement test stands as a pre-registered
pass on significance that fails the bettable clause, which is the clause that
was put there for exactly this case.
