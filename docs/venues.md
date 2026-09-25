# Venues: what each one is, what it costs, and what has actually been read

Started 2026-09-25 in Phase A of the scanner brief
([briefs/2026-09-25-next-task.md](briefs/2026-09-25-next-task.md)). Phase C1
completes it. Every fact here says where it came from and when; anything not
yet read says so. **If a venue's own documentation contradicts this page or the
brief, the documentation wins.**

| venue | in the code as | what it is for | executes? |
|---|---|---|---|
| a sportsbook (Pinnacle + 9 NJ books) | `sportsbook:<book>` | the fair line (Pinnacle), and prices a person could take | never automated; decision support only — the owner clicks |
| Kalshi | `kalshi` | an exchange with no limits on winners and a legal API | paper only in this brief (and its demo environment, if C1 finds one) |
| Polymarket | `polymarket` | a price source | never, in this brief |

Adapters: `scanner/venues/sportsbook.py`, `kalshi.py`, `polymarket.py`. Each
turns its venue's native payload into rows of the one `prices` schema
(`db.py`); nothing downstream knows which venue a price came from except
through the `venue` and `fee_model` columns.

---

## The Odds API (sportsbooks)

**Read 2026-09-25, [v4 docs](https://the-odds-api.com/liveapi/guides/v4/):**

- `GET /v4/sports/{sport}/odds`: cost = markets × regions.
- "Every group of 10 bookmakers is the equivalent of 1 region … between 11 and
  20 bookmakers counts as 2 regions."
- `/v4/sports` and `/v4/sports/{sport}/events` do not count against the quota.
- Responses with no events do not count.
- Headers: `x-requests-remaining`, `x-requests-used`, `x-requests-last` (the
  cost of that call).

**Measured, not just read.** The cloud's pull names Pinnacle plus DraftKings,
FanDuel and BetMGM, one market, and costs exactly 1 credit per sport (its run
logs on 2026-09-24: 473 → 472 → 471 → 470). This contradicts an earlier note in
this project that Pinnacle, being in the `eu` region, doubles every request; it
does not (corrected in `props/collect.py` and `docs/decisions.md`).

**The scanner's list** (`config.ODDS_BOOKS`): Pinnacle, DraftKings, FanDuel,
BetMGM, Caesars (`williamhill_us`), BetRivers, ESPN BET (`espnbet`), Fanatics,
Hard Rock, betPARX — ten books, one region. All ten keys returned prices in the
collector's slates of 2026-09-24. Bally Bet also returned prices and would be
the eleventh, doubling the cost. Three markets (h2h, spreads, totals): **3
credits per sport-wide call.**

**Fee model `book`.** No separate fee: the margin is inside the price. EV of
taking a book's price = fair probability × decimal odds − 1.

## Kalshi

**Read 2026-09-25:**

- **Fees are set per series.** The Series object
  ([API reference](https://docs.kalshi.com/api-reference/market/get-series))
  carries `fee_type` — `quadratic` (the General Trading Fees table),
  `quadratic_with_maker_fees` (plus maker fees), `quadratic_with_combo_maker_fees`
  (maker multiplier 0.5 instead of 0.25), or `flat` (a separate table) — and a
  `fee_multiplier`. The scanner's fee key is `kalshi:<fee_type>:<multiplier>`,
  taken from the series, so a series with its own schedule carries it.
- **Rounding** ([Fee Rounding](https://docs.kalshi.com/getting_started/fee_rounding)):
  the trade fee is the model fee ceiled to $0.000001; the balance change is then
  floored to the member's precision ($0.01 for a non-direct member), and the
  overpayment is rebated later from a per-order accumulator.
- **The order book** ([Get Market Orderbook](https://docs.kalshi.com/api-reference/market/get-market-orderbook)):
  `GET /markets/{ticker}/orderbook` returns `orderbook_fp` with `yes_dollars`
  and `no_dollars`, each a list of `[price, quantity]` strings — **bids only**,
  because a YES bid at X is a NO ask at 1 − X. The docs list authentication as
  required for this endpoint.

**Not read, and why.** The fee schedule itself,
`kalshi.com/docs/kalshi-fee-schedule.pdf` (titled "Fee Schedule for July 2026 -
7.7.26 Update" in search results), returned HTTP 429 to three requests. So:

- The **0.07** taker coefficient, the **0.25** maker share and the **0.5** combo
  share come from the brief, the API docs' description of the tables above,
  and secondary sources — **not from the schedule**. C1 must read it.
- The **`flat`** table is unread. The code raises `UnknownFee` for it, so no
  strategy can compute an EV on a flat-fee series until it is read.
- `conservative=True` (the default) charges the cent rounding and ignores the
  later rebate, so small orders are costed slightly high, never low.

**Still for C1 to establish**, with dates: authentication, market and series
discovery endpoints, rate limits, **whether a demo environment exists**, the
terms of use for automated access, and whether the sports markets are open to
this account. The legal kill switch (`config.KALSHI_SPORTS_ENABLED`, read only
in `scanner.venues.allowed`) exists and is tested; **the monitor check that
alerts if Kalshi starts refusing sports markets for this account needs the
API, and is C2's.**

## Polymarket

**Read 2026-09-25:**

- **Fees** ([Trading fees](https://docs.polymarket.com/trading/fees)):
  `fee = C × feeRate × p × (1 − p)`, **takers only** ("Makers are never charged
  fees"). Taker rate by category: geopolitics 0; finance, politics, mentions,
  tech 0.04; sports, economics, culture, weather, other 0.05; crypto 0.07. The
  page gives no date and does not say which API field carries a market's rate.
  Fee key: `polymarket:<rate>`.
- **The book**: `GET https://clob.polymarket.com/book?token_id=…` returns
  `bids` and `asks` as lists of `{price, size}` strings, per outcome token,
  with a `timestamp` and a `hash`. The adapter sorts levels itself.

**Still for C1:** whether the US-regulated Polymarket is available to a New
Jersey resident, and what its public data API provides. If it is not, the
brief says to mark it `price_source_only` here — which is how the code already
treats it (`scanner.venues.VENUES`: no paper orders).
