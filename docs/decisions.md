# Decisions

One dated entry per decision, in the voice the commit messages use. This is
where the *history* lives, so the code can say what it does now.

The rule: a docstring keeps what the code does, its inputs and outputs, and the
one non-obvious invariant. Everything that begins "this used to", "the previous
version", or "measured on 2026-09-…" belongs here.

---

## 2026-09-25 — The Phase A review: what it changed, and why

The brief required a fresh-session review of Phase A before anything is built
on it. The review found defects in the strategy gates, paper fills, how fair
value decides when a game started, and the polling loop. Every fix tightens
something. None loosens a gate, moves a threshold or touches the schema. What
the rules now are is in [experiments.md](experiments.md) (S0's dated
amendments), [gates.md](gates.md) and [scanner.md](scanner.md). These are
the decisions behind them.

**`realized_ev` fails closed.** The 3-SE bar was simulated on near-normal
values. On a win-or-lose payoff it does not hold: a strategy with no skill
that buys favourites at their fair price passes 5.5% of the time at 80¢,
14.7% at 95¢ and 40.6% at 98¢, because a run of wins has almost no spread.
Rather than guess a new bar, gate 2 records a `realized_ev` strategy's
evidence and fails, until a bar simulated for that payoff is pre-registered.
The consequence: the brief's Phase B promo gate, "realized EV after fees
clear of zero by 3 SE", cannot pass as written. Its bar has to be simulated
and pre-registered first.

**A new definition is a new name.** A strategy's positions, looks and gate-2
record are all filed under its name. So recording a different gate-1
experiment under an old name would carry the old definition's paper record
into the new one. The first fix dropped the old gate-2 record, but the next
scoring re-passed gate 2 on the old positions. `record_backtest` now refuses
a different experiment under an existing name. The new definition is
registered under a new name and starts from nothing.

**A look is claimed before it is taken.** The 3-SE bar holds only at the
cadence it was simulated for, two looks per ET day. A look used to be logged
after its result was written, so a look that was never committed, hit a busy
database, or ran beside another scorer was never counted. Now it is counted
and committed under a write lock before anything is measured. A failure
afterwards costs a look; it can never add one.

**A placebo must place.** Gate 2 refused a strategy whose placebo passed,
but a placebo that placed nothing can never pass, so it blocked nothing. The
placebo now needs 50 graded, settled positions of its own, the same floor
the strategy has.

**Phase B's settlement must settle placebo positions too.** Gate 2 now
counts only positions that are graded and settled, on both sides. Settling
is venue-specific and not written yet. If it settles only `paper` positions,
no strategy can ever reach the placebo's 50.

**A game has one start.** `fair_value` refuses in-play prices, so it has to
know when a game started, and a rain delay moves that pull by pull. The old
rule went wrong two ways. A later start first reported after it had passed
(a feed correcting itself mid-game) turned in-play prices back into pregame
ones. And judging each book on its own let a book missing from the pull that
announced a delay keep the old start and drag the game's start back
(mlb-746c8fcb, 2024-04-03). The start is now judged once for the game, pull
by pull (`sportsbook.next_start`: an earlier start always wins, a later one
only if reported before it passed), and written onto every book market of
the game.

**A-V1 counts delayed-start pulls apart.** With one start per game, a pull
taken after the start it reported can still be pregame. That happens when a
delay was announced only after that start had passed, but before the new
one. `fair_value` prices such a pull. `bets/log.fair_prob` judges each pull
by its own report, so it cannot agree. Making `fair_value` refuse those
pulls would throw away prices that really were pregame. So A-V1 counts them
as "delayed-start, not scored", and it fails on any value priced from an
in-play price. Over the whole archive that is 8 values in 4 games, against
141,638 that agree and 0 that disagree.

**No ledger row, no request.** The loop made the paid request first and
wrote its ledger row afterwards. If that write failed, the call was billed
but never counted, and today's allowance never shrank. Two loops could also
both see room for one more call. Now the check and the call's row, at its
estimate, are committed in one transaction before the request. A database
that cannot be written means no request.

**One ledger of record, and one loop.** The credit limits count the ledger
in the data folder the loop runs against, but every checkout spends the same
paid key, so each data folder had its own 6,000. The loop now runs only
where `data/scanner/ledger-of-record` exists. The owner creates that file by
hand, in production's data folder, when polling is turned on, and nothing
creates it automatically. A running loop also holds an OS lock on
`data/scanner/poll.lock` for its whole life. So a second loop is refused
even while the first one's heartbeat looks old, after a slow tick or a PC
that slept.

**The brief's pace is 0 after its planned days.** The daily pace divides
what is left by the polling days left, and that divisor never went below
one. So from the 43rd polling day on, a single day was offered everything
left of the brief. Phase F runs "four weeks minimum" or until the gate-2
sample is met, so the loop would reach that state. Now, once the 42 planned
days are used, every metered call is refused until the owner extends
`BRIEF_POLL_DAYS` by a commit.

**Paper uses the game's start.** Paper orders, fills, a maker's default
expiry and grading read the order market's own `event_start`, while
`fair_value` read the game's start. For an exchange contract whose own start
is later than the books', that let a paper order fill at a price `fair_value`
refuses. They all use `scanner.fair.game_start` now.

**Then a re-verification.** A second pass re-checked the review's fixes by
attacking each one, and found more. Again every fix tightens something; no
threshold moved and the schema is untouched.

**Gate 2 counts events, not positions.** A strategy's positions on one game
share that game's move, so a second entry is a near-copy of the first, not
new evidence. Nothing stops a strategy from entering the same market on
every pass while its price looks good, and the 3-SE bar was simulated on one
value per game. Counted per position, a strategy with no skill that entered
each game twice passed 15.2% of the time, and ten times 72.3%, against 1.8%
entering once. Gate 2 now takes one value per event, the mean of its
positions, and the placebo is grouped the same way, so both 50 floors count
games. Counted that way it passes 1.7–1.9% however often it re-enters.

**Coverage counts the positions that are due.** For `info`, coverage was
graded and settled ÷ settled. A position that never settles was on neither
side of that fraction, so it could not lower coverage, and a settlement hook
that settles only what it could grade produces exactly that. 60 graded
positions out of 260 resolved read as 100%, and gate 2 passed. Coverage now
counts, for both metrics, the positions whose market resolved more than
36 hours ago, or that have no `resolves_at`. The same 260 give 23%.

**The record pins the definition.** "A new definition is a new name" held
only while nobody edited a strategy's file in place. The gate record held the
experiment but not the metric, and nothing compared the registered strategy
with its record after gate 1. So an edited file kept the old definition's
gates: gate 2 passed again, and `arm()` armed it. The record now holds the
metric too, and `score()` and `arm()` refuse a strategy that no longer
matches its record. Gate 1 also refuses an entry a retired strategy's record
still holds, and the sports' `record()` refuses to write a walk-forward onto
a strategy's block, where it would have turned a failed backtest into a
pass.

**Grading waits for the market to resolve.** Grading at the start froze the
close too early when a delay was announced after the scheduled start had
passed. The start then moves later, but a graded position is never graded
again. In production's archive 134 of 7,414 games had their start moved
later that way, and across them the close moved by a median 0.0037 in
probability (at most 0.0413). A position is now graded only once its market's
`resolves_at` has passed too. For a book game that is the start plus a fixed
game length, and it moves with the delay. A game that was not delayed is
graded later, never differently.

**The marker names its folder.** The ledger-of-record marker was an empty
file that only had to exist, so it travelled with every copy of the data
folder, and copying that folder is routine here. A loop run against a copy
started and made 9 calls, ledgered in the copy's own database, with each copy
free to spend up to the cap. The marker must now contain the full path of
the folder it sits in, so a copy's marker names the original and the copy is
refused. A refused `poll --live` prints the PowerShell line that writes it.

**The loop says when it has stopped.** A loop that held its lock counted as
running however long ago it last wrote its heartbeat, so a loop stuck in one
request read INFO "running" for 8 hours. A held lock with no heartbeat for
15 minutes is now an ERROR naming the process to end: three times the
5-minute window, where the slowest tick measured leaves under 7 minutes
between heartbeats. Past the brief's 42 planned days the loop paused with
nothing in its log and every finding INFO; that is an ERROR now too. And the
lock outranks the heartbeat, so a loop killed just after it wrote one no
longer blocks its own restart, which had meant up to 13.5 hours of no
polling.

---

## 2026-09-25 — Scanner Phase A: the judgment calls

**Built on a branch, not on main.** Production runs `git pull` of `main` at
every scheduled run, so anything pushed to `main` is live within hours. The
brief requires a fresh-session review of Phase A before anything is built on
it; Phase A lives on `phase-a-foundations` until that review says "safe to
build on", and only then merges. Production keeps collecting MLB paper bets
for gate 2 untouched in the meantime.

**The schema change was made without stopping to ask.** CLAUDE.md asks first
for schema changes; the owner's brief specifies `prices` and `markets` column
by column and names the fields of a paper order, and says not to stop for
approval. Treated as the answer. (*Corrected by the Phase A review:* the
other four tables - `credit_ledger`, `paper_fills`, `paper_positions`,
`gate_looks` - are the builder's design, not the brief's.) Kept to what cannot
hurt existing data: new tables and indexes only, no `ALTER`, nothing dropped.
Proved on a copy of production's database (existing definitions and row
counts identical; a second `db.init()` changes nothing).

**The brief's polling cadence is a target, not a schedule.** It costs about
55,600 credits a month for three sports; the brief allows 6,000 in total and
normal operation about 15,000 a month. A fixed ladder of slower cadences, a
daily pace and hard caps, all checked before each call, with one guarantee
kept at every level: each game is priced in its last 30 minutes.

**A strategy's record is a top-level block, the same shape as a sport's.**
So `arm()`, `gates()` and the production guard's `only_paper_changed()` work
unchanged, and a scheduled re-score can always be discarded by the next run.
(*Corrected by the Phase A review:* not always. The guard refuses to discard
a local gate-2 fail when the committed copy holds a pass, because discarding
it would bring the pass back.) `kind: "strategy"` marks it; names may not
collide with a sport's.

**Gate 2 metrics are the brief's two: `info` and `realized_ev`.** Not
`ev_fair_close`, although for a price-taking strategy (E1's sharp-line control,
E2's Kalshi taker) `info` measures forecasting, which they do not claim, and
`realized_ev` is honest but slow. Adding a third, lower-variance metric would
make a gate easier to pass; that is the owner's call at E's pre-registration,
not Phase A's.

**`info` needs the same fair source at both ends.** The review found the MLB
gate mixing consensus at entry with Pinnacle at the close on every graded
bet. For strategies it is ruled out from the start; the MLB gate is unchanged
(still the owner's decision).

**No paper fill after the start.** Found by A-V4 on real prices: 640 of 3,652
pregame orders reached in-play prices - 617 of the 3,629 fills were at an
in-play price, and 23 more orders met one above their limit. `fair_value` already refused those, so
such a position would have been graded against a different market state.
Added to the pre-registration as a dated rule before any strategy existed.

**Kalshi's terms were not read before its adapter was written** (*recorded by
the Phase A review*). The brief says to read Kalshi's API docs *and terms*
before writing a line. The API docs were read (00:40-00:42) before
`scanner/venues/kalshi.py` (00:56); the terms were not, and `docs/venues.md`
lists them as still for C1. The adapter only parses payloads and calls
nothing, so nothing was at risk, but C1 must read and date the terms in
`docs/venues.md` before any Kalshi network call.

---

## 2026-09-24 — Where the history goes

`model/validation.py` carried two simulation tables in comment blocks: the
optional-stopping false-pass rates that set `PAPER_CLV_SIGMA = 3.0`, and the
bets-to-detect-an-edge table in `record_paper`. Both are now in
[gates.md](gates.md), with pointers left in the code.

They were not deleted because they are the *reason* the constants are what they
are, and a threshold without its reasoning is a number someone will eventually
"tidy up". But 40 lines of simulation output in the middle of a scoring
function is a document pretending to be code.

---

## 2026-09-24 — Named book lists are not one region

The note this project carried — "a named bookmaker list counts as one region
however long it is" — is true only *within* a region. Measured, same market,
same endpoint: 11 NJ books + Pinnacle cost **2** credits per market, the 4
offshore books alone cost **1**, all 15 cost **2**.

Cost is the number of **regions** spanned. Pinnacle is `eu`; the US books are
`us`/`us2`. Asking for the sharp anchor doubles every request, and once doubled
the extra books are free. Pinnacle is not optional — it is the fair line the
whole scoring path rests on — so the decision is *pay the two, take all
fifteen*.

**Corrected 2026-09-25.** The cause above is wrong; the numbers are right. The
v4 docs bill a named list as **one region per ten books** ("between 11 and 20
bookmakers counts as 2 regions"), which fits all three measurements, and the
cloud's pull — Pinnacle plus three US books — costs exactly 1 credit per sport
in its run logs. Pinnacle does not double a request; a list longer than ten
does. The collector's fifteen books still cost two per market.

---

## 2026-09-24 — One UTC parser

Three sites parsed a stored timestamp by hand (`bets/log.py` inside
`closing_snapshot`, `cronstatus.py`, `props/collect.py`). All now call
`feeds.parse_utc`, which does exactly the same thing and returns `None` rather
than raising.

The one in `closing_snapshot` mattered most: `ts` carries `+00:00` and
`commence_time` carries `Z`, so a **raw string compare** sorts
`...T23:20:00+00:00` before `...T23:20:00Z` and a snapshot taken just after
first pitch passes as a closing line. Comparing real datetimes is the fix, and
having one parser is what keeps it fixed.

---

## 2026-09-24 — The home intercept (D1)

`margin_to_win_prob` had no intercept, so it returned exactly 0.500 at a
predicted margin of zero — while home teams win ~53% and the mean predicted
margin is about +0.04 runs. Measured out of sample the model under-rated home
teams by ~2.9 points in every season, uniformly across the whole reliability
curve, which is a missing intercept rather than a wrong `k`.

Applied to MLB: helps in all three seasons (−0.00090, −0.00282, −0.00125;
pooled +0.001662, t = +2.98 against the real close; `research/d1_calibration.py`). **Not applied to NFL**,
where the same measurement helps in 2 of 6 seasons and the fitted value flips
sign — the NFL model *over*-rates home teams. `walk_forward(intercept=…)`
therefore defaults to off and MLB opts in.

A bundle saved before this has no `a` and keeps serving exactly the numbers it
was scored with.

---

## 2026-09-23 — Feed matching is on first-pitch time, not date strings

The three feeds mint incompatible game ids for the same game. Matching was on
`(date, away, home)`; it is now on first-pitch time inside a 180-minute window,
with doubleheaders refused rather than guessed.

The threshold was guessed wrong twice before being replaced by the real
invariant — *every feed match is the closest candidate in time* — after 5
minutes missed a 45-minute rain delay and 60 minutes missed a 126-minute one.

---

## 2026-09-23 — The Athletics rename cost 169 games

The Athletics dropped "Oakland" in 2025; the Stats API changed, the odds feed
did not until 2026. Matching on the raw name silently lost 169 games, 7% of the
season — and not a random 7%, one distinctive team entirely absent. Coverage
went 93.0% → 99.7% once `canon_team` existed.

Every feed join now has a canonical name table and a validation step that runs
**before** any spend. The NFL equivalent, `props/nfl_teams.py`, was checked
against a live events list at dry-run time for the same reason; it went with
the finished B3 purchase scripts in the 2026-09-24 review (git history keeps
it - `docs/reports/sweep-pass2.md`).

---

## 2026-09-23 — `predictions` and `features` became append-only

Both tables now keep every row and carry a `feature_row_id` / `prediction_id`,
so a prediction can be traced to the exact feature row and model that produced
it. The consequence, which caught a consumer out: **a row is no longer a
game**. A game scored twice in a day has two rows, and anything counting rows
is counting reruns.

---

## 2026-09-22 — GitHub's scheduled runs are hours late

Measured on this repo: a 14:00 cron fired at 17:58, and a 22:40 cron at 00:53.
No pull is aimed near first pitch, because a late one captures in-play prices,
which are worthless as a CLV anchor. It is also why the Phase 0 collection runs
from the local machine rather than Actions.

The mode router matches the **hour only**; it used to compare the whole cron
string, so changing a minute would silently have turned the morning pull into a
close pull.

---

## 2026-09-22 — `rest_days` computed two different things

`rest_days(df, "home_ab")` tracked only the home column, so it measured days
since the team's last **home** game — a homestand-length feature. The live path
had always measured days since the last game of any kind. 16% of rows
disagreed, mean 4.04 days against 2.71.

Training and serving computing different features under one name is the quietest
failure mode in this project; `audit.py` now recomputes `rest_days` both ways
from the games table on every run.

---

## 2026-09-22 — `status='final'` is terminal, and a NULL never overwrites

Both ingest paths once wiped completed scores when a feed re-reported a game as
not started. In every upsert now, `final` is terminal and a NULL never replaces
a real value.

Related: `backfill.py` filtered on `abstractGameState` rather than
`feeds.stats_api_status`, and `abstractGameState` is `"Final"` for a postponed
game too. Fixed 2026-09-24 in the review (`a0f5389`), with the upsert brought
under the same final-is-final rule.

---

## 2026-09-21 — `pandas.sort_values` is not a stable sort

Re-sorting an already date-sorted frame reshuffles same-day games. Assigning a
merge result back positionally after that scrambled 98% of the bullpen and
offense features and went unnoticed for weeks. **Join on explicit keys,
always.**
