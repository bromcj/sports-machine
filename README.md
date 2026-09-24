# Sports Machine

Predicts who wins baseball games, and refuses to bet on itself until it can
prove it beats the bookmakers. Three checks, enforced in `bets/engine.py` — the
program returns `bet: False`, it does not merely advise against one.

**Status: not cleared to bet — and now for a measured reason rather than a
missing measurement.** MLB has been scored against real de-vigged closing lines
for 2024–2026 and **lost in all three seasons** (−0.0082 pooled, t = −4.86,
n = 6,533). NFL lost in all four of its test seasons. NFL receiving props were
bought and tested too, and lost by five to eleven standard errors.
`python model/validation.py` prints the current state.

- **The whole picture in two pages:**
  [docs/state-of-the-machine.md](docs/state-of-the-machine.md)
- **What was tested, and what it found:**
  [docs/experiments.md](docs/experiments.md) — written before anything was fit
- **Running it:** see [COMMANDS.md](COMMANDS.md)
- **Looking at it:** `python dashboard.py`
- **Checking it:** `python audit.py` — 86 checks against live data
- **Keeping it:** `python backup.py` — verified snapshot of the database

## Setup (once)

1. `pip install -r requirements.txt`
2. `python db.py`
3. Key from the-odds-api.com → `ODDS_API_KEY` env var. One odds pull costs
   one credit **per in-season sport**.
4. Optional: push to GitHub with `ODDS_API_KEY` as a repo secret, and Actions
   collects odds three times a day whether your machine is on or not.
   Scheduled workflows need a public repo, or Pro on a private one.

This project runs **two separate keys**. The cloud's is the repo secret, on the
free 500-credits-a-month tier (its run log said `Credits left: 480` on
2026-09-24); three pulls a day with all four sports in season is a projected
372 in October (4 × 3 × 31), the tightest month. The key in the owner's Windows
user environment is a paid one (41,742 of 100,000 left on 2026-09-24), spent by
the props collector and by anything else that pulls odds on that PC.
[COMMANDS.md](COMMANDS.md#what-costs-api-credits) lists what spends which.

## The three gates

Recorded in `validation.json`, enforced by `bets/engine.py:evaluate()`:

| Gate | Passes when | Recorded by |
|---|---|---|
| `walk_forward` | model beats a **real** de-vigged market in every test season, the pooled margin is more than 2 SE above zero (t > 2), and dropping any one season still leaves t ≥ 1 | `model/validation.py:record()` |
| `paper_trading` | 50+ graded paper bets whose mean **info** (how far the de-vigged price moved the bet's way between bet and close — not raw CLV, which includes line shopping) clears 3 SE; at least 75% of settled paper bets graded; no one ET start-time slot over 60% of graded bets unless 90%+ are graded; and a random-side placebo run through the same pipeline must **not** also pass | `record_paper()` |
| `armed` | a human calls `arm()`, which refuses until the first two pass | `arm()` |

Beating a **placeholder** baseline clears nothing, however large the margin.
That rule used to be the reason MLB was blocked: it beat its home-rate constant
in all three test seasons, which meant only that it had learned baseball. Real
closing lines have since been bought, and MLB now **loses to them in all three
seasons** — so the block stands for the stronger reason. No record means not
cleared, so a fresh checkout or a cloud runner refuses by default.

`evaluate(allow_unvalidated=True)` exists for backtests that must score an
uncleared model deliberately. It is never set on a path that stakes money.

## Why the gates exist

The NFL model beats a home-team baseline decisively and picks 62–66% of games
correctly. Run its picks through the 4% edge threshold and it fires on **74% of
all games**, claims a mean **+12.5%** edge, and returns **−9.3%** over 808
simulated bets.

`min_edge` measures *disagreement* with the price. That is only worth money
when the model forecasts better than the price does. When it forecasts worse,
the disagreement is noise and the threshold sells it back as confidence.

## Pipeline

```
ingest/      odds (The Odds API) · scores (ESPN) · probables (MLB Stats API)
backfill.py  5 seasons of Statcast + schedules; `topup` keeps the live season current
features/    build_training.py (history) · build.py (today's unplayed games)
model/       train.py (walk-forward) · persist.py (saves the fitted model)
             predict.py (scores today) · validation.py (the gates)
bets/        engine.py (no-vig, Kelly, guardrails) · log.py (CLV grading)
             paper.py (places/settles/scores paper bets for gate 2)
dashboard.py one-page visual summary → dashboard.html / .png
backup.py    verified database snapshots; cronstatus.py checks the cloud
audit.py     86 checks: leakage, identity, staleness, de-vig, gates,
             backups, and that ingest rejects impossible values
healthcheck.py  writes STATUS.md, fails the cloud run if something is wrong
```

Only MLB is wired end to end. NFL has a walk-forward model
(`features/build_training_nfl.py`, `features/sports/nfl_features_v1.py`), built
to have something real to test against back when football had years of public
closing lines and baseball had none. It is tested, not served: nothing saves
or predicts with it. NBA and NHL have no model at all; their odds are
collected in season (config.py) and nothing else.

## Three things that are easy to get wrong

**The feeds mint incompatible game ids.** The MLB Stats API calls a game
`mlb-823494`; The Odds API calls the same game `mlb-394e1e2b…`; ESPN calls it
`mlb-espn-401817028`. Measured on a copy of the live database (2026-09-24):
about 12,100 games carry a final score, about 7,400 carry odds, and **zero
carry both**. `feeds.odds_twin()` matches them on team names (canonicalised,
so a renamed club still matches) plus first pitch within 180 minutes — never on
dates, which the feeds disagree about for late games. A straight doubleheader
(two games of the same feed, same teams, inside that window) is refused rather
than guessed; a split doubleheader is told apart by its start times.

**Rolling features must exclude the game they describe.** Every window is
`shift(1)`-ed at source. `audit.py` re-derives one from raw Statcast every run
rather than trusting the code.

**Never assign a merge result back positionally.** `pandas.sort_values`
defaults to a non-stable quicksort, so re-sorting an already-date-sorted frame
reshuffles same-day games. That once scrambled 98% of the bullpen and offense
features. Join on explicit keys.

## Build order per sport (do not skip ahead)

1. Backfill 3–5 seasons; wire the sport's feature module.
2. Walk-forward must beat the **real** no-vig market out of sample.
3. Paper-trade until 50+ graded bets show positive info, clear of the noise.
4. Call `arm()`. Then money.

Validate one sport end to end before wiring the next. Four half-validated
models are worse than one proven one.
