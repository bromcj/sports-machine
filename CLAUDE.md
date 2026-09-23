# Working in this repo

A multi-sport betting model that **refuses to bet until it can prove it beats
the bookmakers**. See [README.md](README.md) for what it is and
[COMMANDS.md](COMMANDS.md) for how to run it.

The owner built this with AI help and is not a professional programmer.
Explain in plain English, say what you actually verified, and don't assume
familiarity with jargon.

## Never, without asking

- **Don't spend API credits casually.** Only `morning` and `close` cost
  anything: one Odds API credit **per in-season sport**, against a 500/month
  cap already running ~360 in October. Never run either just to test a change.
  Free and safe any time: `picks`, `audit`, `dashboard`, `grade`, `refresh`,
  `backup`, `cronstatus`, `validation`, `healthcheck`, `merge_archive`.
  (`refresh` is free but slow — it re-downloads Statcast and retrains.)
  To exercise the cloud workflow end to end, dispatch it with `mode=grade` —
  that path pulls no odds.
- **Don't rewrite or delete `archive/` history.** Those CSVs are the only
  record of what prices existed at what moment. They are append-only.
- **Don't touch the API key handling.** It reads from an env var and a repo
  secret. Leave it alone.
- **Ask before changing the database schema.** `db.py` migrates in place;
  a careless `ALTER` or a dropped column is not recoverable from `archive/`.
- **Don't rebuild a whole module.** If that's where you're heading, stop and
  report what you found instead.
- **Don't weaken the gates.** `bets/engine.py` returns `bet: False` until all
  three pass. That refusal is the product, not an obstacle. Making it easier
  to bet is never the fix.

## Before you claim anything is fixed

`python audit.py` must come back **28 passed, 0 failed**. It re-derives its
answers from live data rather than trusting comments, and it is the fastest
way to know whether a change broke something.

**Verify before asserting.** Reproduce the bug before fixing it, and prove the
fix. Several confident claims in this project's history turned out to be
wrong — a "leakage bug" that wasn't, a set of "locked" git files that were
read-only by design, a calibration change predicted to help that made things
worse. Each cost the owner real time. A one-minute check beats a plausible
story.

If you were wrong, say so plainly in one sentence and move on.

## Traps this codebase has already fallen into

- **`pandas.sort_values` is not a stable sort.** Re-sorting an already
  date-sorted frame reshuffles same-day games. Assigning a merge result back
  positionally after that scrambled 98% of the bullpen and offense features
  and went unnoticed for weeks. **Join on explicit keys, always.**
- **The three feeds mint incompatible game IDs.** MLB Stats says
  `mlb-823494`, The Odds API says `mlb-394e1e2b…`, ESPN says
  `mlb-espn-401817028` — for the same game. Effectively zero games carry both
  a score and odds. `bets/log.py:odds_twin()` resolves them on
  `(date, away, home)`; doubleheaders are ambiguous and are refused, not
  guessed.
- **Rolling features must exclude the game they describe.** Every window is
  `shift(1)`-ed at source. `audit.py` recomputes one from raw Statcast each
  run rather than trusting the code.
- **`min_edge` measures disagreement, not edge.** It only pays when the model
  forecasts better than the price. The NFL model picks 62–66% of games right,
  claims +12.5% mean edge, and returns −9.3% over 808 simulated bets.
- **GitHub's scheduled runs are hours late**, not minutes. Measured here: a
  14:00 cron fired at 17:58. Any schedule aimed close to first pitch will
  capture in-play prices, which are worthless as a CLV anchor.
- **Timestamps go in as aware UTC**, via `db.utc_now()`. Mixing naive and
  aware datetimes raises `TypeError`, not a wrong answer.
- **Finished is final.** In every upsert, `status='final'` is terminal and a
  NULL never overwrites a real value. Both ingest paths once wiped completed
  scores when a feed re-reported a game as not started.

## Practical

- `data/` is gitignored and is the only copy of anything. `python backup.py`
  before anything risky.
- Shell heredocs mangle `\n` and nested quotes in this environment. Use the
  Write/Edit tools for anything with escapes — several attempts were lost to
  this.
- Commit messages here explain **why**, with the evidence that motivated the
  change. Match that.
- The owner is US Eastern. Translate UTC cron times to their clock.
