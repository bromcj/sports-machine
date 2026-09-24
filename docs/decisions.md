# Decisions

One dated entry per decision, in the voice the commit messages use. This is
where the *history* lives, so the code can say what it does now.

The rule: a docstring keeps what the code does, its inputs and outputs, and the
one non-obvious invariant. Everything that begins "this used to", "the previous
version", or "measured on 2026-09-…" belongs here.

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
pooled +0.001662, t = +2.98 against the real close). **Not applied to NFL**,
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
