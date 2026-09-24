# Phase 3 — sharp vs soft, as a control

`bets/sharp_line.py`, `python run_daily.py shop`. It never stakes money and
never feeds a gate.

## The control passes

This is the part that mattered most. The engine is **+EV by construction** —
Pinnacle's de-vigged price is the fair probability, so a book paying more than
that is a positive-expectation bet by arithmetic. If it reported a negative
expectation over a real sample, the fair-line construction or the grading path
would be broken, and every number in this project that leans on `fair_prob`
would be suspect.

Reimplemented independently and run against the four-book archive on E4's own
terms (within 90 minutes of first pitch):

| book | this engine | E4 published |
|---|---|---|
| DraftKings | **−4.21%** | −4.24% |
| FanDuel | **−3.79%** | −3.81% |
| BetMGM | **−4.35%** | −4.40% |
| games offering >1.5% EV | **1 in 105** | 1 in 104 |

Two independent implementations agree to within four hundredths of a percent.
**The fair-line and grading paths are sound.**

### The first run disagreed, and the disagreement was mine

It initially reported −2.69% and 258 flags per 100 games, which looks like a
contradiction of E4 and is not. Two definition mismatches:

1. **E4 restricted to within 90 minutes of first pitch** — what a live shop
   would actually be looking at. Scanning every snapshot at every lead time
   includes prices days out, where books disagree far more and nobody is
   standing at a terminal.
2. **E4 counted GAMES offering a worthwhile price**, not raw flags. One game
   produces several flags — several books, two sides, several timestamps — so
   the two numbers differ by an order of magnitude while describing the same
   market.

Both are now matched, and both are reported, so a *real* disagreement would be
visible instead of drowned in a definitional one. A control that can be made to
agree by choosing a definition is not a control.

## The open question: was "nothing to shop" true, or under-observed?

E4 measured four books. The Phase 0 pulls name **fifteen**, and all fifteen
return prices. First scan of what has been collected:

```
32 games, 15 books
flags: 10  (31.2 per 100 games)
median lead: 189 h before kickoff
```

**That number is not comparable to E4's and the report says so.** A median lead
of 189 hours means these are prices *eight days* out, where books have not yet
converged and nobody is shopping. The comparable near-close figure needs the
collection to run for a couple of weeks — which is exactly what the brief asks
for, and the schedule is now running.

What can be said today: with fifteen books there **are** materially mispriced
lines on the board a week ahead — FanDuel at +360 on Arizona where Pinnacle's
fair price implies +7.7% EV, DraftKings at +350 on the same game. Whether any
of that survives to kickoff is the question the next two weeks answer.

## Reporting

Flags are logged separately and kept out of every model report, for the reason
the Phase 5 EV decomposition exists: **shopping value and forecasting value are
different things, and a model must never be credited with the first.**

## Documented

`COMMANDS.md` and the integration matrix in the same commit —
32 entry points, 0 undocumented.

---

## One golden-test re-baseline, and why

After the Phase 1.7 merge the golden test failed with exactly two differences:

```
validation_json.mlb.paper_trading: missing in baseline
validation_text: "paper-trading FAIL (nothing recorded)"
               -> "paper-trading FAIL (only 1 graded paper bets, need 50)"
```

**That is the machine working, not a regression.** The scheduled job ran on
prod for the first time since the intercept was applied, settled and graded a
real paper bet, and gate 2 now records a state instead of a blank. Every other
pinned behaviour — 3,459 twin resolutions, 27 predictions, the `market_close`
checksum, the full decomposition of all bets, monitor's ten checks, the audit
text — was **unchanged**.

Re-baselined, and `capture.py` now says explicitly that `validation.json` is a
**record** rather than behaviour: a difference there has to be *explained*
before the baseline is re-captured. If the explanation is "the machine ran",
re-baseline and say so here. If there is no explanation, something moved that
should not have. **Re-baselining without reading the diff is how a golden test
becomes decoration.**
