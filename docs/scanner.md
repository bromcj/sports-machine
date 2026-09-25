# The scanner: how it works

Built in Phase A of [briefs/2026-09-25-next-task.md](briefs/2026-09-25-next-task.md).
The machine's job changes from predicting games to **finding prices that are
wrong, proving it on paper, and acting only where nobody limits winners**.
Phase A is the foundation; it makes no money and places no order of any kind.
What each venue is and costs: [venues.md](venues.md). The gates:
[gates.md](gates.md). The rules fixed in advance:
[experiments.md](experiments.md), "Scanner brief".

**Switched off.** `config.POLLING_ENABLED = False`: nothing polls, nothing
spends a credit. No strategy exists yet. Both arrive in later phases, each by
a deliberate commit.

## The pieces

| file | what it does |
|---|---|
| `db.py` | seven new tables: `markets`, `prices` (append-only), `credit_ledger`, `paper_orders`, `paper_fills`, `paper_positions`, `gate_looks`. Additive only — no existing table touched |
| `scanner/venues/` | one adapter per venue, native payload → `markets` + `prices`; `allowed()` is the single place the Kalshi legal kill switch is read |
| `scanner/store.py` | market keys, checked inserts, one timestamp shape, "the next observation after this moment" |
| `scanner/fees.py` | what a trade costs, per venue; every EV is after fees |
| `scanner/fair.py` | `fair_value()` — the only fair price |
| `scanner/budget.py` | the credit ledger, three limits, the cadence and its fallback ladder |
| `scanner/poll.py` | the polling loop and the supervisor the scheduled job runs |
| `scanner/paper.py` | paper orders, fills, positions, settlement, grading |
| `scanner/capital.py` | stake, days to resolution, annualized EV, capital locked |
| `scanner/strategies/` | the registry — a strategy is a file |
| `scanner/gates.py` | each strategy's gate 1 and gate 2 |
| `scanner/scoreboard.py` | per strategy, per venue, in total |

## One price schema

A **market** is one venue's contract on one question: DraftKings' spread at
−3.5 and at −3 are two markets. Its `canonical_event_id` is a `games` row (for
sportsbooks, the odds feed's own `<sport>-<event id>` row — the id
`ingest/odds.py` already mints), so other venues join to it through `feeds`
matching (start time plus names), never a date string. A non-game market gets
a key of its own.

A **price** is one side of one market at one moment: `outcome` (home, away,
over, under, yes, no), `quote` (`ask` = what buying it costs; `bid` = what
selling it fetches), `level` (1 = best; order books keep three), `price` as a
probability, `price_native` exactly as received, `size_available` (NULL for a
book), `fee_model`, `captured_at` (when we saw it) and `source_last_update`
(when the venue last moved it). Prices are never updated or deleted; the same
observation twice is ignored.

## One fair price

`fair_value(con, market_id, outcome, at)` answers, in order: **Pinnacle**
de-vigged at the latest pull at or before `at`; else the **consensus** — the
mean de-vigged price across every book with both sides at that pull; else the
market's own **mid**. It returns the source, the moment of the prices used and
their age, and refuses (returns nothing) rather than:

- read anything after `at`;
- use a price captured at or after the game's start (in play) — using the
  latest start the feed has reported, because rain delays move it;
- use a price older than `max_age_s` when the caller sets one;
- price a spread from a book at a different line.

A Kalshi yes/no contract on a game is priced from the books through
`markets.yes_outcome`. The de-vig is `bets.engine.novig_probs` on the prices as
quoted — the arithmetic `bets/log.fair_prob` uses — and `audit.py` proves the
two agree exactly on every recent pregame pull (A-V1: 1,926 of 1,926 on dev's
data, 2,012 of 2,012 on a copy of production's).

## Polling and its budget

The brief's cadence, by time to the soonest game not yet started: within 90
minutes every 2 minutes; 90 minutes to 6 hours every 5; 6–24 hours every 15;
beyond that hourly; once everything has started, nothing. One call returns a
sport's whole slate for **3 credits** (three markets, ten books = one region).

That cadence cannot be afforded. `python run_daily.py poll --plan`, for a
typical week with NFL, NBA and NHL all in season:

| | credits | cadence the governor picks |
|---|---|---|
| the brief's own cadence | ~55,600 a month | — |
| **the brief**: 6,000 over 42 polling days (143 a day) | ~4,410 projected | ladder levels 4–5: closing prices every 20–30 min, 1–6 h out every 1–2 h |
| **normal operation**: 12,000 a month (395 a day) | ~10,300 a month, + 3,000 props = ~13,300 of 15,000 | levels 2–3: closing every 10–15 min, 1–6 h every 20–30 min |

So the loop steps down a fixed ladder (`scanner/budget.py LADDER`), slowing
the far-off tiers first. **Every level that polls at all still prices every
game in its last 30 minutes** — tested as a property over random slates — so
every game gets a close inside `CLOSING_WINDOW_MIN` (60).

Before every metered call, three limits are checked from `credit_ledger`: the
brief's 6,000, the month's 12,000, and a daily pace (what is left divided by
the polling days left; unspent credits roll forward). A call with no response
counts at its estimate. A metered call is never retried automatically. Every
call is logged to `credit_ledger` and to `api_usage`, so `monitor.py` sees the
account balance; it also warns at 25% and alerts at 10% left of the brief's
cap and of the month's budget.

The scheduled job runs `poll --ensure` twice a day: start the loop if it
should be running, restart it on newly pulled code, and leave a loop that
stopped on a refused key for a person. Whether it is running is judged by its
heartbeat file (`data/scanner/poll.json`), never by probing a process id.

## Paper execution

`paper.submit()` records an intended order and refuses anything that is not
`paper` or `placebo` (the database refuses it too), a venue the kill switch or
the price-source rule closes, an order whose fees cannot be priced, and
anything over the strategy's daily exposure cap (`config.STRATEGY_DAILY_EXPOSURE`
— worst-case loss including fees, per ET day, checked before the order exists).

`paper.simulate()` fills only against prices observed **after** the order:

- a **taker** takes the first observation strictly after it, level by level up
  to its limit, never more than the size shown; the rest is cancelled;
- a **maker** fills only when a later observation trades *through* its price
  (best ask strictly below its bid), at its own price, within the size shown;
- **nothing fills at or after the game's start**; an order still open then
  expires.

A-V4 ran taker orders at every real capture time of production's last week
(3,652 orders): zero fills used a price from at or before the order, every
fill was the first observation after it — and, once the start rule above was
added, zero fills were in play (before it, 617 were).

Each position records contracts, average price, fee, stake, days to
resolution, fair value and EV at the fill, and
`annualized_ev = ev / days_to_resolution × 365`.

## Strategies

A strategy is a file in `scanner/strategies/` that registers a name, venues,
a signal, a placebo, a metric and the experiment that is its gate 1. Its
gates are in [gates.md](gates.md). None exists in Phase A.

## Commands

See [COMMANDS.md](../COMMANDS.md): `poll --plan | --status | --ensure | --live`,
`strategies [--score]`, and the scanner section of `scoreboard`.
