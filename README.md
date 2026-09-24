# Sports Machine

Predicts who wins baseball games, and refuses to bet on itself until it can
prove it beats the bookmakers. Three checks, enforced in `bets/engine.py` — the
program returns `bet: False`, it does not merely advise against one.

**Status: not cleared to bet — and now for a measured reason rather than a
missing measurement.** MLB has been scored against real de-vigged closing lines
for 2024–2026 and **lost in all three seasons** (−0.0099 pooled, t = −5.5,
n = 6,519). NFL lost in all four of its test seasons. NFL receiving props were
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
3. Key from the-odds-api.com → `ODDS_API_KEY` env var.
   The free tier is 500 credits/month; one pull costs one credit **per
   in-season sport**. Three pulls/day is ~360 in October when all four
   overlap, which is the tightest month.
4. Optional: push to GitHub with `ODDS_API_KEY` as a repo secret, and Actions
   collects odds three times a day whether your machine is on or not.
   Scheduled workflows need a public repo, or Pro on a private one.

## The three gates

Recorded in `validation.json`, enforced by `bets/engine.py:evaluate()`:

| Gate | Passes when | Recorded by |
|---|---|---|
| `walk_forward` | model beats a **real** de-vigged market in every test season | `model/validation.py:record()` |
| `paper_trading` | 50+ graded paper bets, mean CLV clearing 3 SE, across 3+ start-time buckets | `record_paper()` |
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

Only MLB is wired end to end. NFL has a trained model
(`features/sports/nfl_features_v1.py`) built to have something real to test
against, since football has years of public closing lines and baseball does
not yet. NBA and NHL are feature contracts only — `build_row()` returns all
`None`.

## Three things that are easy to get wrong

**The feeds mint incompatible game ids.** The MLB Stats API calls a game
`mlb-823494`; The Odds API calls the same game `mlb-394e1e2b…`; ESPN calls it
`mlb-espn-401817028`. Measured on the live database: 12,000+ games carry a final
score, a few dozen carry odds, and **zero carry both**. `bets/log.py:odds_twin()` resolves
them on `(date, away, home)`. Doubleheaders are ambiguous and are refused
rather than guessed.

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
3. Paper-trade 50+ picks to positive CLV.
4. Call `arm()`. Then money.

Validate one sport end to end before wiring the next. Four half-validated
models are worse than one proven one.
