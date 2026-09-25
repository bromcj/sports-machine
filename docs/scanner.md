# The scanner: how it works

Built in Phase A of [briefs/2026-09-25-next-task.md](briefs/2026-09-25-next-task.md).
The machine's job changes from predicting games to **finding prices that are
wrong, proving it on paper, and acting only where nobody limits winners**.
Phase A is the foundation; it makes no money and places no order of any kind.
What each venue is and costs: [venues.md](venues.md). The gates:
[gates.md](gates.md). The rules fixed in advance:
[experiments.md](experiments.md), "Scanner brief".

**Switched off.** `config.POLLING_ENABLED = False`: `poll --live` refuses and
`poll --ensure` starts nothing, so nothing polls and nothing spends a credit.
A loop started while polling was on never rereads the flag. The next
scheduled `--ensure`, after its `git pull`, asks it to stop, and `monitor.py`
reports an ERROR until it has. Creating `data/scanner/poll.stop` stops it at
its next check, about every 30 seconds. No strategy exists yet. Polling and
strategies both arrive in later phases, each by a deliberate commit.

## The pieces

| file | what it does |
|---|---|
| `db.py` | seven new tables: `markets`, `prices` (append-only), `credit_ledger`, `paper_orders`, `paper_fills`, `paper_positions`, `gate_looks`. Additive only — no existing table touched |
| `scanner/venues/` | one adapter per venue, native payload → `markets` + `prices`; `allowed()` is the single place the Kalshi legal kill switch is read |
| `scanner/store.py` | market keys, checked inserts (a known venue, that venue's own fee model, times that parse, `first_seen` present, no size on a book row, a book's price equal to its own moneyline; a row that fails is counted as a reject and not stored, and no check crashes mid-batch), one timestamp shape, "the next observation after this moment" |
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
book; the store refuses a book row that has one), `fee_model`, `captured_at`
(when we saw it) and `source_last_update`
(when the venue last moved it). Prices are never updated or deleted; the same
observation twice is ignored.

## One fair price

`fair_value(con, market_id, outcome, at)` answers, in order: **Pinnacle**
de-vigged at the latest pull at or before `at`; else the **consensus** — the
mean de-vigged price across every book with both sides at that pull; else the
market's own **mid**. It returns the source, the moment of the prices used and
their age, and refuses (returns nothing) rather than:

- read a price captured after `at`. (The start it judges "in play" by may
  come from a later pull. That only ever refuses more, except for a delay,
  which counts only if it was announced before the delayed start; see the
  next line.)
- use a price captured at or after the game's start (in play). The start is
  `scanner.fair.game_start()`: **one start per game**, judged pull by pull by
  `scanner.venues.sportsbook.next_start` and written onto every book market
  of the game, including books missing from that pull. An earlier start
  always wins. A later start (a rain delay) counts only if it was reported
  before it passed. A later start first reported after it had passed is the
  feed correcting itself mid-game, and is ignored. An exchange contract's own
  start caps it when that is earlier. The market's own mid obeys the same
  cutoff;
- use a price older than `max_age_s` when the caller sets one;
- price a spread from a book at a different line;
- price an outcome the market does not have. A book market has only its two
  sides. An exchange contract is priced from the books only through a
  `yes_outcome` that is one of their sides, and otherwise only from its own
  mid.

A Kalshi yes/no contract on a game is priced from the books through
`markets.yes_outcome`. The de-vig is `bets.engine.novig_probs` on the prices as
quoted — the arithmetic `bets/log.fair_prob` uses — and `audit.py` proves the
two agree exactly on every recent pregame pull, and that every in-play pull
is refused (A-V1: 1,926 agree and 370 of 370 in-play refused on dev's data;
2,012 agree and 354 of 354 on a copy of production's). Over the whole archive
it is 141,638 agree, 0 disagree, 24,942 of 24,942 in-play refused. Another 8
values, in 4 games, are counted apart: pulls taken after the start they
reported, in games whose delay was announced only after that start had
passed, so they are pregame to `fair_value` but not to `fair_prob`
([experiments.md](experiments.md), A-V1).

## Polling and its budget

The brief's cadence, by time to the soonest game not yet started: within 90
minutes every 2 minutes; 90 minutes to 6 hours every 5; 6–24 hours every 15;
beyond that hourly; once every game of the sport has started, nothing. One
call returns a sport's whole slate for **3 credits** (three markets, ten books
= one region).

That cadence cannot be afforded. `python run_daily.py poll --plan`, for a
typical week with NFL, NBA and NHL all in season:

| | credits | cadence the governor picks |
|---|---|---|
| the brief's own cadence | ~55,600 a month | — |
| **the brief**: 6,000 over 42 polling days (143 a day at first; unspent credits roll forward) | up to 6,000 — the whole cap, reached on the last polling day | each day starts at ladder level 4–5 (closing prices every 20–30 min, 1–6 h out every 1–2 h) and steps up as the day goes |
| **normal operation**: 12,000 a month (395 a day at first) | up to 12,000 a month, + 3,000 props = 15,000 of 15,000 | each day starts at level 2–3 (closing every 10–15 min, 1–6 h out every 20–30 min) and steps up as the day goes |

So the loop steps down a fixed ladder (`scanner/budget.py LADDER`), slowing
the far-off tiers first. **Every level that polls at all still prices every
game in its last 30 minutes** — tested as a property over random slates — so
every game gets a close inside `CLOSING_WINDOW_MIN` (60).

It does not hold one level all day at a flat pace. It re-picks its level
every 5 minutes against what is left of the day, and a day's allowance is
what is left divided by the polling days left, so it spends up to its caps.
(*Corrected 2026-09-25 by the Phase A review:* this table used to show
~4,410 for the brief and ~10,300 + 3,000 = ~13,300 a month. Those were the
planner's figures for one level held all day, which is not what the loop
does.)

Before every metered call, three limits are checked from `credit_ledger`: the
brief's 6,000, the month's 12,000, and a daily pace (what is left divided by
the polling days left, today included; unspent credits roll forward).

- **No ledger row, no request.** The check and the call's ledger row, at its
  estimate, are written and committed in one transaction *before* the
  request. The row is filled in with the real cost afterwards. If the
  database cannot be written, no request is made. A result that cannot be
  written still counts at its estimate, and two loops cannot both spend the
  last credits.
- **The pace.** Because today counts as one of the days left, the brief's last
  planned polling day, and a month's last day, may use whatever is left. Once
  the brief's 42 planned polling days are used, the pace is 0: every metered
  call is refused (limit "brief") until `config.BRIEF_POLL_DAYS` is extended
  by a commit.
- **Paused, not stopped.** When today's allowance cannot cover even the
  slowest level (the brief or the month has less than one call left, or the
  planned days are used), the loop stays paused and makes no metered request.
  It does not stop. `monitor.py`'s credit findings are the alert. A call
  that the brief's cap or the month's budget refuses outright stops the loop.
- **Whose ledger.** The limits count the ledger in the data folder the loop
  runs against, but every checkout spends the same paid key, so a loop in a
  second folder would start again from a fresh 6,000. So the loop runs only
  where `data/scanner/ledger-of-record` exists. The owner creates that file
  by hand, in production's data folder, in the step that turns polling on.
  Anywhere else `poll --live` refuses and `--ensure` starts nothing.

A call with no response counts at its estimate. A metered call is never
retried automatically. Every call is logged to `credit_ledger` and to
`api_usage`, so `monitor.py` sees the account balance; it also warns at 25%
and alerts at 10% left of the brief's cap and of the month's budget.

**In-play prices are stored, never used.** One call returns the sport's
whole slate, so games already under way come back too. Their prices are
stored in `prices` (they cost nothing extra), but `fair_value`, paper orders,
fills and grading all ignore anything captured at or after a game's start
(`scanner.fair.game_start`). Anything new that reads `prices` must apply the
same cutoff.

The scheduled job runs `poll --ensure` twice a day: start the loop if it
should be running, restart it on newly pulled code, and leave a loop that
stopped on a refused key for a person. Whether it is running is judged by its
heartbeat file (`data/scanner/poll.json`) and by an OS lock on
`data/scanner/poll.lock`, never by probing a process id. `poll --live` holds
that lock for its whole life, and the OS drops it however the process ends,
so only one loop can run: a second is refused even while the first one's
heartbeat is old. The heartbeat names the commit the loop started on. On
newly pulled code, `--ensure` asks the old loop to stop and starts the new
one only once it has. If the old loop has not stopped within about
2 minutes, `--ensure` says "NOT restarted" and the next scheduled run starts
the new code.

## Paper execution

`paper.submit()` records an intended order and refuses anything that is not
`paper` or `placebo` (the database refuses it too), a venue the kill switch or
the price-source rule closes, an order whose fees cannot be priced, and
anything over the strategy's daily exposure cap (`config.STRATEGY_DAILY_EXPOSURE`
— worst-case loss including fees, per ET day, checked before the order exists).
It also refuses:

- an order at or after the game's start (`scanner.fair.game_start`, the same
  start `fair_value` uses), or at or after the market's `resolves_at`;
- a maker whose limit is at or through the ask showing when it is placed (it
  would take, not rest);
- a taker with an `expires_at` (a taker never rests);
- a size that is not a finite positive number, or a limit not strictly
  between 0 and 1.

Its fees, exposure and EV are priced on the fee model showing at the order's
moment, not on one first seen later.

`paper.simulate()` fills only against prices observed **after** the order:

- a **taker** takes the first observation strictly after it, level by level up
  to its limit, never more than the size shown; the rest is cancelled;
- a **maker** fills only when a later observation trades *through* its price
  (best ask strictly below its bid), at its own price, within the size shown;
- a size shown is filled **once per strategy and mode** (paper and placebo
  apart). An unchanged offer in a later poll is the same offer, even when
  better offers push it below the three levels stored;
- Kalshi fees are rounded once per order, as Kalshi rebates them, and a fill
  that would take the stake past the order's recorded exposure is not made;
- **nothing fills at or after the game's start** (`game_start` again); an
  order still open then expires.

A-V4 ran taker orders at every real capture time of production's last week
(3,652 orders): zero fills used a price from at or before the order, every
fill was the first observation after it — and, once the start rule above was
added, zero fills were in play (before it, 617 were).

Each position records contracts, average price, fee, stake, days to
resolution, fair value and EV at the fill, and
`annualized_ev = ev / days_to_resolution × 365`.

`paper.grade()` scores `info` only at or after the game's start, taking the
close at that start. Before it, the position waits. A graded position is
never graded again.

## Strategies

A strategy is a file in `scanner/strategies/` that registers a name, venues,
a signal, a placebo, a metric and the experiment that is its gate 1. That
experiment is its own entry: no two strategies may share one, and a new
definition takes a new name. Its gates are in [gates.md](gates.md). None
exists in Phase A.

## Commands

See [COMMANDS.md](../COMMANDS.md): `poll --plan | --status | --ensure | --live`,
`strategies [--score]`, and the scanner section of `scoreboard`.
