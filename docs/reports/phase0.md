# Report point 0 — Phase 0 collection: cost, schedule, what was enabled

Run 2026-09-24. **Enabled as written; no thinning needed.** Projection
**1,850 credits/month**, against a 3,000 cap for this brief.

## What was measured, and one correction to a note this project was carrying

The brief (and my own note from the last session) said a named bookmaker list
counts as one region however long it is. **That is only true within a region.**
Measured today, same market, same endpoint:

| named list | cost per market | books returned |
|---|---|---|
| 11 NJ books + Pinnacle | **2** | 11 |
| 4 offshore books alone | **1** | 4 |
| all 15 | **2** | 15 |

Cost is the number of **regions** the named books span, not the number of
books. Pinnacle sits in `eu`; every US book is `us`/`us2`. So **asking for the
sharp anchor doubles every request** — and once it is doubled, the four
offshore books are free.

Pinnacle is not optional: it is the fair line the entire scoring path is built
on, and B2 exists because asking for it the wrong way returns no error. So the
decision is *pay the two, take all fifteen*, and every number below carries the
×2.

## Cost

| stream | per week | per month | basis |
|---|---|---|---|
| NFL player props | 64 | 277 | 16 games × 2 markets × 2 regions, one snapshot each |
| NFL game markets | 20 | 87 | 5 pulls × 2 markets × 2 regions, one request per slate |
| NBA player props | 315 | 1,365 | ~7.5 games/day × 3 markets × 2 regions, one snapshot each |
| NBA game markets | 28 | 121 | 2 pulls/day × 1 market × 2 regions |
| **total** | **427** | **1,850** | |

**NFL only, until NBA opens in late October: 364/month.** The NBA line does not
start spending until opening night.

Against 41,747 credits remaining this month (expiring) and a 20K plan from next
month, 1,850/month fits with room. Under the cap, so the schedule runs as
written — no Saturday dropped, NBA stays at two pulls a day.

## Schedule

Five identical daily triggers, ET: **11:45, 15:45, 18:00, 20:00, 21:00**.

This is deliberately dumber than the brief's league-specific list, and it is
the one judgment call worth flagging. The brief asked for Thu 6pm / Sat 8pm /
Sun 11:45 / Sun 3:45 for NFL and two a day for NBA. Instead the task fires at
five fixed times every day and **the collector decides**: it pulls a game only
when kickoff is inside the lead window (NFL 5 h, NBA 6 h) and the game has not
already been recorded at a nearer time.

Why: the events list is **free**, so an idle run costs nothing — verified, a
run with nothing due spends 0. A schedule that has to be kept in step with two
league calendars, bye weeks, flexed kickoffs and the NBA's variable slate is a
schedule that drifts out of step silently. This one cannot. Every game still
gets exactly one snapshot, taken at the scheduled time closest before its
kickoff, which is what the brief actually wanted.

One consequence, stated plainly: **Thursday 6pm was specified partly to see
whether Pinnacle had posted by then**, since B2 found it had not by noon. The
lead-window rule captures TNF at the 18:00 trigger, which answers that. It does
**not** take an early-week snapshot of Sunday's games, so it does not by itself
reopen the opener question — which is out of scope for this brief anyway.

## What is collected

- **NFL**: `player_receptions`, `player_reception_yds` per event;
  `h2h`, `spreads` for the slate.
- **NBA**: `player_points`, `player_rebounds`, `player_assists` per event;
  `h2h` for the slate. Collect only, no model.
- **Injury report**, timestamped, at every NFL pull — saved as its own dated
  file rather than merged into a rolling table, because the question a January
  test asks is who was ruled out *at pull time*, and a table that gets
  corrected later cannot answer it.
- Raw payloads gzipped under `data/props_live/raw/`, every request logged to
  `requests.jsonl`, credits to the existing `api_usage` table.

**No schema change.** Phase 1 has not built its golden test yet, and altering
the live schema before there is a way to prove nothing moved would be the wrong
order. Everything lands in files.

## The credit cap, enforced in code

`props/collect.py:Budget` reads `x-requests-last` and `x-requests-remaining`
off every response and refuses any request that would take the running total
past the cap — **checked before the request, not after**, because a cap you
discover you have passed is not a cap.

## Verified end to end

A bounded live test (cap 30, lead window widened to 26 h) pulled the Thursday
night game: **12 books, 8 credits, injuries snapshotted, raw payload written**.
A normal run with nothing due spends **0**.

## Pre-registration

`docs/experiments.md` entry **F1**, written today, **blind to 2026 data** —
before a single 2026 prop price existed. It fixes the population (leading
receiver by prior-four-week target share ruled `Out`), the metric, the pass
rule, the predicted direction, and the two model fixes that must be made before
the test and cannot be tuned on 2026 data: the negative-binomial dispersion
function and the `vacated_share` join.

## Spent in Phase 0

**16 credits** — 8 on the bounded end-to-end test, 8 on the three region
measurements. Running total against the 3,000 cap: **16**.
