# sports_machine — Technical Audit

Reviewed at commit `408cb01` (2026-09-23), all ~6,000 lines, plus the 14 archive CSVs in the repo.
Your local database (`data/`) isn't in the repo, so anything that needs it is marked **[needs DB]**. Where I say **confirmed**, I reproduced it from code plus archive data. Where I say **by inspection**, the code logic shows it but I couldn't run it.

---

## 1. Executive Assessment

**What's already strong, and better than most hobby betting systems:**

- **It fails closed.** No record means no bet. Beating a placeholder clears nothing. Arming requires a human. And `bet: False` is enforced in code rather than advised. Most betting projects are missing exactly this safety mechanism.
- **It fights leakage at the source.** Rolling features are shifted where they're built, joins use explicit keys, a row-count assertion guards the merges, and `audit.py` re-derives a feature from raw Statcast instead of trusting the code.
- **Ingestion is careful.** Retries are scoped (5xx and timeouts only, never 4xx on a metered API). A `final` row can't be un-finalized. NULLs never overwrite values. Impossible values are rejected at the boundary. A UNIQUE index enforces dedupe.
- **Operations are honest.** Backups are verified, merges are idempotent and watermarked, a healthcheck fails the run loudly, and the documentation explains *why*.

**The biggest weaknesses:**

1. **Game identity across feeds is the Achilles heel.** Three feeds use three IDs and two date conventions, and they're joined on `(date, away, home)`. I confirmed that this attaches **the previous night's odds** to every late West Coast game in a series (C1).
2. **Live-season results never arrive.** No scheduled job pulls MLB Stats API finals, so paper bets can't settle, gate 2 can't fill, and the 2026 training data stops at the backfill date (C2). Postponements are also recorded as impossible 0-0 finals that can never be corrected (C3).
3. **You can't reproduce a past prediction.** Predictions and features are overwritten in place, and the model version isn't unique (C7).
4. **Gate 2 measures the wrong quantity** and re-tests itself every night (C6, from the previous review).
5. **The model is thin and ignores the market.** Eleven public features, predictions compressed around 54%, and no path yet to test it against a real MLB market. This is solvable now for about $59 (§6, §13).
6. **Development and production share one folder.** The unattended job runs `git pull --autostash` inside the working copy you and Claude Code edit (C8).

Overall: the *engineering instincts* are strong. What's weak is the layer where data from different sources and times meets: identity, timing and lineage. That layer decides whether every downstream number is true, so it's where to spend the next week.

---

## 2. Architecture Map

```
                    CLOUD (GitHub Actions, 3x/day, starts 2-4h late)
  The Odds API ──► ingest/odds.py ──┐
  ESPN scoreboard ► ingest/scores.py ├─► fresh runner SQLite ─► export_snapshots.py ─► archive/*.csv ─► git commit
  MLB Stats API ─► ingest/mlb.py ───┘                          healthcheck.py ─► STATUS.md (red run = email)

                    LOCAL PC (Task Scheduler 11:30am / 10pm, same folder you develop in)
  git pull ─► merge_archive.py ─► data/machine.db (SQLite: games, odds_snapshots, features, predictions, bets)
  backfill.py (Statcast, one-time + topup) ─► data/statcast/{year}.parquet
  backfill_nfl.py (nflverse) ─► data/nfl/*.parquet

  TRAINING (manual/weekly):  build_training.py ─► mlb_features.* ─► model/train.walk_forward ─► validation.record (gate 1)
                                                                  └► model/persist.save ─► data/models/mlb.joblib
  LIVE:   run_daily predict: topup_statcast ─► features/build.build_for_date ─► features table (JSON)
                             ─► model/predict.predict_for_date ─► predictions table
          run_daily paper:   bets/paper.place  (predictions × _odds_for twin match × engine.evaluate(allow_unvalidated))
                             bets/paper.settle (games.final × closing_snapshot twin match × log.grade)
                             bets/paper.score  ─► validation.record_paper (gate 2)
          run_daily picks:   model/predict.picks ─► terminal table;  dashboard.py ─► dashboard.html
  REAL MONEY: engine.evaluate refuses unless walk_forward ∧ paper_trading ∧ armed
```

**Where state lives:**

- `data/machine.db` exists only on your PC and is backed up to OneDrive.
- `data/statcast`, `data/nfl` and `data/models` exist only on your PC but can be rebuilt.
- `archive/` holds the cloud's odds and schedules and is public in git.
- `validation.json` holds the gate decisions and is also in git.

**Where APIs are called:**

- The Odds API costs 1 credit per sport per pull.
- ESPN, the MLB Stats API, Statcast (pybaseball) and the nflverse GitHub releases are all free.

**Unused or half-connected pieces:**

- `features/registry.py`: nothing imports it (documented).
- The `bettime` snapshot type is never written.
- `MAX_DAILY_PCT` is defined and never enforced.
- `GUARDRAIL_FLAGS` exist, but no caller ever passes flags, so no guardrail can fire.
- `calibrate.reliability_table` is never called.
- `nfl_data_py` and `nba_api` are required but not used.
- `pyarrow` is used by every parquet read and write but isn't in `requirements.txt`.
- NBA and NHL are "active" and cost odds credits, but have stub features only. That's fine as deliberate data collection; see §11.

---

## 3. Critical Issues

### C1. Late games are matched to the previous night's odds (confirmed)

- **Problem:** `ingest/odds.py` stores `game_date = commence_time[:10]`, which is the **UTC** date. The MLB Stats API stores the **local** date. Any game starting at 8pm ET or later (00:00 UTC) gets an odds-feed date one day later. `_odds_for()` and `odds_twin()` match on `(date, away, home)`. So when the same teams also played the night before, Wednesday's game matches **Tuesday's odds row**, which is dated Wednesday in UTC.
- **Evidence (archive):** SD @ LAD has odds rows dated 2026-09-23 (first pitch 09-23T02:11Z, i.e. **Tuesday** 10:11pm ET) and 2026-09-24 (Wednesday). Wednesday's Stats API game is dated 09-23, so it gets Tuesday's prices. The same happens for AZ @ COL and LAA @ ATH.
- **Where:** `ingest/odds.py:pull_sport` (the date), `model/predict.py:_odds_for` and `bets/log.py:odds_twin` (the match).
- **Why it matters:**
  - Paper bets on late games are priced against the wrong game, with different starters, and are then graded against the wrong game's close.
  - These are exactly the games that qualify for CLV grading (the only closes inside 60 minutes are 9:40pm+ ET starts). So most of gate 2's graded sample would be mismatched games.
  - The doubleheader check can't catch it, because only one odds row matches.
  - The first game of a series gets "no odds" and is silently skipped.
  - The comment "verified, 15 of 15 on a full slate" was true on a day without the problem.
- **Fix:**
  - Store `start_time_utc` for every game from the MLB Stats API (`gameDate`) and ESPN (`date`).
  - Match odds events on `(away, home, |commence_time − start_time_utc| < 3h)`, which also resolves doubleheaders by time.
  - Keep `game_date` meaning the **local** (ET) calendar date everywhere.
  - Better still, add a `game_xref(canonical_game_id, feed, feed_game_id, match_method, matched_at)` table so the match is made once, stored and auditable, instead of re-derived by every caller.
  - Add a regression test built from the SD @ LAD rows above.

### C2. Live-season finals never arrive (confirmed)

- **Problem:** Stats API rows become `final` only via `mlb.pull_day(today)`. The cloud runs it at ~2pm, ~7pm and ~10pm, before night games end, and never for yesterday. The local scheduled job never calls it. `grade` isn't scheduled.
- **Evidence:** across every archived games file, **0 of 34** Stats API games reached `final` (all `live` or `preview`). ESPN rows do reach `final`, but settlement looks up the Stats API ID.
- **Why it matters:**
  - `bets/paper.settle()` requires `games.status='final'` for the bet's own ID, so paper bets essentially never settle and gate 2 can never fill. The previous assistant's "wired end to end" was overstated.
  - The 2026 training data also freezes at the backfill date (`SEASON_DATES[2026]` ends 09-21).
- **Fix:**
  - In the local job, pull `mlb.pull_day()` for **yesterday and today** (and `scores.pull_all(yesterday)`).
  - Add a monitor: "games that started more than 12h ago and aren't final" should raise an ERROR.

### C3. Postponements become permanent, impossible finals (confirmed row, cause by inspection)

- **Problem:**
  - ESPN reports postponed games with state `post`, and `scores.py` converts a missing score to `0`. The archive has **TOR @ BAL 2026-09-22 recorded `final` 0-0** (`mlb-espn-401817035`), which can't happen in MLB.
  - The MLB Stats API reports postponed games with `abstractGameState = "Final"` (the detailed state says "Postponed"), so `mlb.py` also marks them `final`.
  - Because "final is terminal", the make-up game, which keeps the same `gamePk` on a new date, can **never** get its score or its new date.
- **Why it matters:**
  - Make-up game results are lost.
  - Paper bets on them never settle.
  - `rest_days_asof()` counts a postponed date as a game played, so live rest days disagree with training.
  - `quality.game()` lets 0-0 through.
- **Fix:**
  - Use Stats API `detailedState` / `codedGameState` and ESPN `status.type.name` to map to `scheduled | live | final | postponed | suspended | cancelled`.
  - Treat only real completions as final.
  - Let `game_date` and `start_time_utc` update while a game isn't final.
  - Reject `final` with 0-0 for MLB (and any final with a NULL score).
  - One-time repair: find `final` rows with NULL or 0-0 scores.

### C4. Statcast top-up freezes partial same-day data (by inspection; verify)

- **Problem:** `topup_statcast()` fetches `last_date+1 … today`. The 10pm job calls it, so it downloads **today's in-progress games** (Savant publishes during games). Tomorrow's run starts at `today+1`, so today's night games stay truncated forever. It's the late innings that go missing, which is **bullpen** data.
- **Why it matters:**
  - It biases every live bullpen and offense window for the next 30 days.
  - It only affects the live path, since training uses complete backfills. So it's pure train/serve skew, and nothing would flag it.
- **Verify [needs DB]:** pitches per game for recent night games. A complete game is about 280-300 pitches.
- **Fix:**
  - Cap the top-up at **yesterday**.
  - Always re-fetch the last 3 days (dedupe already keeps the last copy).
  - Add a check: any game in the parquet with fewer than 200 pitches, or with a 9th inning missing, raises a WARNING.

### C5. The 10pm job places paper bets on games already underway (confirmed from code)

- **Problem:** `scheduled_check.bat` runs `predict` then `paper` at 10pm. `build_for_date` includes non-final (live) games. `place()` never checks first-pitch time. `_odds_for` returns the latest pregame price, which is the same snapshot `settle()` later uses as the close.
- **Why it matters:**
  - These are bets nobody could have placed.
  - Their CLV is exactly 0, and they count toward the 50-bet floor with no information behind them.
  - The 10pm run also overwrites the 11:30am prediction for the same game (see C7).
- **Fix:**
  - `place()` skips any game with `start_time_utc − now < 10 min`. `build_for_date` builds only `status='scheduled'`.
  - Stamp every bet with the odds snapshot ID it used.
  - Assert `snapshot.ts ≤ bet.ts < start_time`.

### C6. Gate 2 measures the wrong thing (from Review 2)

- **Same-book close includes the vig.** CLV against the same book's closing price, vig included, isn't expected value. A bet can beat it and still lose money. Grade against the **Pinnacle no-vig close**: `EV = decimal_taken × p_fair_close − 1`.
- **Best-of-4-books selection biases CLV upward.** Archive estimate: +0.18 points (16 games, 90% CI −0.13 to +0.51).
- **Nightly re-testing inflates false passes.** A zero-skill model passes at least once in 14% of seasons (17% over two seasons), or about 50% of seasons with the bias above.
- **Fix:** grade against the fair close; add a random-side placebo that must fail; require 3.0 SE for nightly checks (2.2% over two seasons); require the placebo to fail.

### C7. You can't reproduce a prediction or a bet

- **Problem:**
  - `features` and `predictions` use `INSERT OR REPLACE` keyed on `game_id`, so every rerun erases the previous one.
  - `model_version = "ridge-mlb-<trained_through>"` is identical for two different fits on the same data.
  - Nothing records the git commit, the Statcast data version, or which odds snapshot a decision used.
- **Why it matters:** when a result looks too good or too bad, you can't answer "what did the system know at the time?" Right now it can't even tell you which of the two daily predictions a paper bet came from.
- **Fix:** make the tables append-only, as in §15: `predictions(prediction_id, game_id, created_at, model_id, code_sha, feature_row_id, …)`, `features(feature_row_id, game_id, created_at, payload, inputs_through)`, `models(model_id = sha256(file), trained_at, code_sha, data_through, metrics)`, and `bets.prediction_id` plus `bets.odds_snapshot_id`.

### C8. Production runs in your development folder

- **Problem:** the unattended job runs `git pull --rebase --autostash` and then executes code **in the same checkout** you and Claude Code edit. A half-finished edit at 11:29am becomes production at 11:30am, and an autostash conflict can leave the folder mid-rebase while you're working in it.
- **Fix:**
  - Keep a second clone (for example `C:\sports-machine-prod`) that only ever runs `git pull` of `main` and the scheduled jobs. Point both at the same `data/` folder through an environment variable, or keep the database in the prod folder.
  - Develop in the other clone and push when `audit.py` is green.

### Also high severity (not data-invalidating today because nothing bets, but they will be)

- **H1. Guardrails can never fire.** `evaluate()` supports `sp_unconfirmed`, `opener_flag` and `pitch_limit_flag`, but no caller passes flags. The engine's docstring promises protections that don't exist. Compute `sp_unconfirmed` from the time since the probable was last confirmed, and `opener_flag` from the probable's recent innings per appearance.
- **H2. The daily exposure cap isn't enforced.** `MAX_DAILY_PCT` is unused.
- **H3. Venues are wrong for the Rays.** Tampa Bay played 2025 at Steinbrenner Field (a different, hitter-friendly park), but `VENUES` only handles the A's. Their 2025 home games got a Tropicana factor, and 2026 inherits a mixed history. Neutral-site series would get the "home" team's park. Take `venue.id` from the Stats API schedule instead of a hand-maintained map.

---

## 4. High-Impact Improvements (ranked)

| # | Improvement | Impact | Effort | Urgency |
|---|---|---|---|---|
| 1 | Start-time-based game identity + `game_xref` (C1) | Critical | Medium | Critical |
| 2 | Pull yesterday's finals; status taxonomy; postponement repair (C2, C3) | Critical | Low | Critical |
| 3 | Statcast top-up capped at yesterday plus a 3-day re-fetch (C4) | High | Low | Critical |
| 4 | Only bet pregame; bet↔snapshot linkage (C5) | High | Low | Critical |
| 5 | Append-only predictions/features/models with code SHA (C7) | High | Medium | High (painful to add later) |
| 6 | **Model-vs-market on every game**, daily (§13 idea 1) | High | Low | High |
| 7 | Gate 2 on fair-close EV, placebo, 3 SE (C6) | High | Medium | High |
| 8 | Separate prod checkout (C8) | Medium | Low | High |
| 9 | pytest unit suite + CI on every push | High | Medium | High |
| 10 | Buy historical odds once and run MLB gate 1 against a real market | High | Low ($59) | Medium |
| 11 | Store raw API payloads, market `last_update`, credits remaining | Medium | Low | High (can't recover later) |
| 12 | Probable-pitcher and lineup history tables | Medium | Low | High (can't recover later) |
| 13 | Guardrails wired, daily cap enforced (H1, H2) | Medium | Low | Medium (before arming) |
| 14 | Pin dependencies, add `pyarrow`, match CI and local Python | Medium | Low | Medium |

---

## 5. Data Engineering Improvements

**Ingestion**

- **Use the free `/events` endpoint before spending credits.** It doesn't count against the quota. Skip a sport's odds pull when it has no events in the next 24h (NBA and NHL in early October, MLB after the World Series). That saves 25-40% of October credits.
- **Store the market-level `last_update`** from the Odds API. The pull timestamp isn't when the book last moved its price, and a stale book looks like an edge. The bookmaker-level field is deprecated, so use the market-level one.
- **Log `x-requests-remaining` and `x-requests-used` to a table** (they're printed today). A warning at 20% of the monthly budget left beats discovering a 429 halfway through a slate.
- **Keep the API key out of logs.** On a 4xx, `raise_for_status()` produces a message containing the full URL, `apiKey=…` included, and `odds.pull` prints it. GitHub masks secrets in Actions logs, but local logs don't. Strip query strings from printed errors.
- **Fix the date conventions everywhere:** ESPN `ev["date"][:10]` is also a UTC date. Store `start_time_utc` plus `local_date`, and never derive one from the other with string slicing.
- **Save raw responses** as gzipped JSON under `data/raw/<source>/<yyyy-mm-dd>/<pull-ts>.json.gz`. That's a few MB a month, and it lets you re-parse history when you add a market, a field or a fix. It's the cheapest insurance in this list.

**Storage (what you actually need)**

- **Now: keep SQLite plus parquet.** They fit a single-writer, one-machine, gigabyte-scale workload.
  - Add WAL mode (`PRAGMA journal_mode=WAL`) so the dashboard can read while a job writes.
  - Add `PRAGMA foreign_keys=ON` once `game_xref` exists.
- **Later, only if analytical queries get slow:** add **DuckDB** as a *read* engine over the SQLite file and parquet. It needs no server and no migration.
- **Not needed:** Postgres, object storage, or a separate cache layer. The one real single point of failure is `bets` / `predictions` in one file, and the verified OneDrive backups already cover it.
- **Git as the data bus from cloud to PC is fine for years at this volume** (~180 odds rows per pull). Revisit if you add markets or intraday pulls.

**Data quality edge cases to handle explicitly**

| Case | Status today | Needed |
|---|---|---|
| Late games (UTC date) | **Broken (C1)** | Start-time matching |
| Postponed / suspended / resumed | **Broken (C3)** | Status taxonomy; mutable date until final |
| Doubleheaders | Refused (safe) | Resolve by start time, not refuse |
| Relocated / neutral venues | Only the A's handled (H3) | Stats API `venue.id` |
| Team renames | Hand map | `teams(team_id, feed, name, valid_from, valid_to)` |
| Probable changes / scratches | Last value kept, history lost | Timestamped probable history |
| Openers / bulk pitchers | Not detected | Flag from innings per appearance |
| Postseason | Stats API ingest includes it; model is regular-season only | Exclude or flag `game_type` |
| Early-season windows | "30-day" windows reach back to last September in training (§3 leakage L1) | See §6 |
| Stale bookmaker prices | Not visible | Store `last_update` |
| Impossible finals (0-0, NULL) | Allowed | Reject |

**Lineage (can you trace a prediction today?)**

| Question | Today |
|---|---|
| Where its data came from | Partly (feature payload has `_statcast_through`) |
| When it was collected | Partly (overwritten on rerun) |
| Which model made it | No (version not unique) |
| Which features | Only the latest rerun |
| Which odds were available | No link |

The design in §15 answers all five.

---

## 3b. Leakage and Look-Ahead Audit (detail)

"None found" means I traced the code path and it's clean.

| # | Suspect | Verdict | Where | Severity | Fix |
|---|---|---|---|---|---|
| L1 | Early-season rolling windows | **Real skew.** `rolling("30D")` then row `shift(1)` means the first game of a season takes the window ending at the team's *last game of the previous season*, i.e. September data labelled "30-day". `rest_days` for opening day is about 170. Live skips these games because its date-based window is empty. The "early-window games dropped" message implies they're dropped; they aren't (except in 2022). | `mlb_features.bullpen_table/offense_table`, `build_training.rest_days` | Medium: trains on mislabelled rows that live never sees | Mask windows whose span crosses the off-season (require data within 30 calendar days of the game), cap `rest_days` (e.g. at 5) and add `season_game_number`; or explicitly blend prior-season ratings with shrinkage (§6) |
| L2 | Actual starter vs probable | **Train/serve skew.** Training uses the pitcher who actually threw first; live uses the announced probable. Openers are "starters" in training. | `mlb_features.pitcher_game_lines`, `features/build._row` | Medium | Record probable history (§11); in training use the probable known at, say, T−3h where available; add an opener flag |
| L3 | Window anchoring | Training windows anchor at the team's previous game; live anchors at today. They diverge after off-days. | same | Low | Build training features with the live `*_asof` functions (one code path) |
| L4 | Partial same-day Statcast | Live-only corruption (C4) | `backfill.topup_statcast` | High | Cap at yesterday; re-fetch 3 days |
| L5 | Same-day pitches entering features | **None found.** `_asof` filters `< asof` | `features/build` | none | none |
| L6 | Scaler/preprocessing before split | **None found.** It's inside the pipeline, fit per fold | `model/train` | none | none |
| L7 | Hyperparameter tuning on test | **None found.** Alpha is tuned on the last training season | `model/train` | none | none |
| L8 | Park factors | **None found.** Prior seasons only (but see H3 venues) | `park_factor_table` | none | none |
| L9 | Closing odds used when unavailable | Not in modeling. In NFL gate 1, closing lines are the *benchmark*, which is correct. In paper trading, C5 lets a bet be "placed" at a price captured after the decision point. | `bets/paper.place` | High (C5) | Pregame-only; bet↔snapshot link |
| L10 | Wrong-game prices | C1: past information attached to the wrong game | `_odds_for`, `odds_twin` | Critical | C1 |
| L11 | NFL `k` fit on in-sample predictions | Mildly optimistic `k` (claimed −0.0003 effect) [needs DB] | `build_training_nfl` | Low | Out-of-fold, like MLB |
| L12 | Retroactive data revisions | Statcast top-up keeps the *last* copy, so revised stats silently rewrite history; nflverse and ESPN can also restate | `topup_statcast` | Low | Version raw data (§11); record the data version in features |
| L13 | Repeated testing on the same seasons | Every feature experiment re-scores 2024-26. Over many experiments you're fitting the test seasons (garden of forking paths). | process | Medium over time | Experiment log; freeze a final holdout (§6) |

---

## 6. Modeling and Statistical Improvements

**Is the current model sensible?** As a baseline, yes. Ridge regression on run differential is stable, fast and understandable. What limits it isn't the algorithm, it's the information it gets.

1. **Check the margin-to-probability mapping for a missing intercept [needs DB].** `p = sigmoid(k·margin)` has no intercept. MLB home teams win 52-55% but outscore opponents by only about 0.1-0.2 runs, partly because the home team skips the bottom of the 9th when leading. A ridge intercept of ~0.15 runs × k 0.3 gives about 51%. So the model may *systematically under-rate home teams*.
   - Check: mean predicted home probability vs the actual home win rate, per season.
   - Fix: fit `p = sigmoid(a + k·margin)` out of fold, or model `home_won` directly with logistic regression.
   - Expect a gain of 0.0005-0.002 in log loss if the intercept is off. That's small but free.
2. **Challenger to add: logistic regression on `home_won`** with the same features plus a calibration check (the unused `reliability_table`). Test it with the same walk-forward and a paired per-game log-loss difference. Keep whichever wins with t > 2 on *untouched* data.
3. **Shrink noisy inputs.** Five starts of K-BB% is about 120 batters faced, which is mostly noise. Use longer windows blended toward a prior, e.g. `(K-BB)_shrunk = (PA·x + 200·league_mean)/(PA + 200)`, with the PA count carried as a feature or a no-bet filter. The same applies to 30-day bullpen and offense windows early in the season.
4. **Add a team-strength prior.** An Elo or rolling run-differential rating, regressed to the mean between seasons, captures what 30-day windows can't. Of the "classic" additions, this one is the most likely to help.
5. **The largest structural gain is anchoring on the market.** Once you have historical odds: `logit(p) = logit(p_market_at_bet_time) + β·x`. Your features then only need to explain *where the market is wrong*. If β isn't significantly different from zero out of sample, you've learned the features carry no edge, which is also valuable to know.
6. **Keep sports separate.** MLB (starter-driven, low scoring, run-line quirks), NFL (QB-driven, few games) and NBA/NHL (availability-driven) should each own their pipeline. A shared *interface* (`build_features(date) → frame`, `predict(frame) → prob`) is enough. Don't build a generic framework.
7. **Realistic expectations.** A sharp MLB closing market sits around 0.680 log loss (Pinnacle archive estimate: 0.681). Your model is at 0.686-0.690. Public-feature MLB models rarely beat the *close*. Where edges exist is earlier prices and softer books, which is why the evaluation should be "at bet-time price vs the fair close", not just "vs the close".

**Validation framework I'd use:**

- **Walk-forward by month within seasons,** retraining monthly (the same cadence you'd use live). That gives about 18 folds instead of 3, and it tests early-season behavior explicitly.
- **Paired per-game log-loss difference vs (a) the best constant and (b) the market,** with a CI from a block bootstrap by **day**, and a leave-one-season-out check.
- **An experiment log** (`experiments.csv`: date, idea, features, fold metrics, decision). Before arming anything, one final confirmation on a **holdout you haven't touched**: the next season's live games, or 2025 frozen from now on.
- **Live:** every game, every day, model vs the Pinnacle no-vig close (§13 #1).

---

## 7. Betting-System Improvements

- **Keep model quality and betting profitability separate:**
  - **Model quality:** log loss and Brier vs the market, calibration by decile.
  - **Profitability:** EV at bet price vs the fair close, ROI with a bootstrap CI, maximum drawdown, and bet count.
  - Report both, never ROI alone. At about 300 bets, a 95% CI on MLB moneyline ROI is roughly ±10%, so a "profitable backtest" of that size is compatible with a losing strategy.
- **Test edge monotonicity.** Bucket paper bets by claimed edge (3.5-5%, 5-8%, 8%+). If realized fair-close EV doesn't rise with claimed edge, the edge is noise. This diagnostic would have caught the NFL problem.
- **Shrink before betting:** `p_bet = w·p_model + (1−w)·p_market`, with `w` fit out of sample. This is usually 0.2-0.4 for public-data models, and it's the honest way to stop 74%-of-games firing.
- **De-vig method:** proportional is fine near even odds. For heavy favorites, power or Shin de-vig is more accurate. Low priority, but cheap to add behind a function.
- **Assert pregame timing:** every bet stores `odds_snapshot_id`, `snapshot.ts ≤ bet.ts < start_time`, and minutes to first pitch.
- **Correlation:** one bet per game is already enforced. Keep it until you add other markets for the same game.
- **Bankroll:**
  - Quarter Kelly with a 3% cap is reasonable, but only as good as the probability going in. Stake on the *shrunk* probability.
  - Enforce `MAX_DAILY_PCT`.
  - Add a drawdown stop, e.g. pause at −15% from peak (the CLV kill switch is good; add a money one).
  - Keep staking in `engine.py`, separate from prediction. It already is.
- **NO BET is the correct default and already is.** Add three reasons to prefer it: starter sample below N, data stale by more than 1 day, and match method other than exact.

---

## 8. Performance Improvements

Real, not premature:

- `build_for_date` loads **every Statcast season** (millions of pitches) and recomputes `pitcher_game_lines` with Python-lambda `groupby` aggregations **every run** to produce 30-day windows. That's slow now and scales linearly.
  - Precompute `pitcher_game_lines` as a derived parquet, updated incrementally by the top-up.
  - Replace the lambdas with boolean columns plus `sum`.
  - Load only the current and previous seasons for live.
- The `starter_table` per-pitcher loop is fine at this size.

Premature, so skip for now: parallel ingestion, async HTTP, caching model predictions, database tuning beyond WAL.

---

## 9. Reliability and Monitoring

The cloud has a healthcheck. **The local jobs, where predictions and paper bets actually happen, have none.** They write a log file that nobody reads. Every critical issue above would have been caught by an alert.

| Level | Trigger (examples) |
|---|---|
| **CRITICAL** (stop, notify now) | Any bet whose price came from a snapshot after first pitch; a twin match with \|commence − start\| > 3h; a real-money bet from an uncleared sport; database integrity check fails; newest backup fails verification |
| **ERROR** (notify) | A started game still not final after 12h; paper bets unsettled after 36h; a scheduled job didn't run; a 0-0 or NULL-score final; odds pull 4xx or 429; credits below 10% |
| **WARNING** (daily digest) | Statcast stale by more than 1 day; a recent game with fewer than 200 pitches; more than 20% of the slate skipped (and why); feature mean more than 3 SD from its 30-day mean; mean predicted home probability outside 50-56%; book `last_update` more than 60 min old at bet time; credits below 25% |
| **INFO** | Run summaries: rows ingested, games predicted, bets placed or settled, credits used |

Delivery:

- One `monitor.py` run at the end of each local job, writing `logs/health.jsonl` (structured, one line per check).
- Anything at ERROR or above triggers a notification. The simplest option that fits is an email via Gmail SMTP with an app password, or an ntfy.sh push.
- Include "the job didn't run at all": the cloud workflow can check that the local machine committed a heartbeat file in the last 26h.

---

## 10. Code Architecture Improvements

- **Make it a package.** Add `pyproject.toml`, run modules with `python -m sports_machine.…`, and remove the ~15 `sys.path.insert` hacks. Right now import behavior depends on the folder you run from.
- **Split `audit.py` in two:**
  - `tests/` (pytest): pure functions with no database. That covers odds conversion, de-vig, quality rules, pooling math, `record_paper`, twin matching (C1 fixture), status mapping (C3 fixture), top-up date ranges (C4) and the start-time guard (C5). Run it in CI on every push.
  - `audit.py`: live-data invariants only.
  - Today no test runs in CI, and a change can reach `main` with only a local audit behind it.
- **One configuration surface.** Thresholds live in five places (`config.py`, `engine.py`, `log.py`, `validation.py`, `mlb_features.py`). Keep Python config (it's fine), but collect betting, gating and feature-window constants in `config.py` sections. Paths should come from one `paths.py` that honors `SPORTS_MACHINE_DATA_DIR` (needed for C8).
- **Dependencies:**
  - Add `pyarrow`, and drop `nfl_data_py` and `nba_api` until they're used.
  - Pin exact versions in a lock file (`pip-compile` or `uv lock`).
  - CI uses Python 3.11 but your PC runs 3.14. Pick one.
- **Comments are becoming a changelog.** About half the lines are comments, many narrating past bugs ("used to…", "the previous version…"). That's great context for an AI assistant, but it makes the *current* behavior harder to see. Move history into commit messages (you already write good ones) and a short `docs/decisions.md`, and keep docstrings about what the code does now.
- **Remove or implement dead parts:** `registry.py`, the `bettime` snapshot type, unused guardrails.
- Once C1 is fixed, `_odds_for` and `odds_twin` should be one function.

---

## 11. Things to Start Collecting NOW (can't be recovered later)

Historical *facts* (scores, pitches, schedules) can be re-downloaded. **What the world looked like before a game can't.** Priorities:

1. **Odds at more points in time.** Cover open, a mid-day snapshot, and **~45 min before each first-pitch cluster**, with the market-level `last_update`. Include Pinnacle always. This is the dataset that makes every future evaluation possible. (Paid historical odds cover 2020 onward at 5-10 min intervals, so older gaps can be bought, but your own dense near-close snapshots are cheaper going forward.)
2. **Raw API responses** (gzipped), so you can re-parse history when you add a market or a field.
3. **Probable-pitcher history:** every pull's probables with a timestamp. Scratches, and when they became known, are unrecoverable and move lines more than anything else.
4. **Confirmed lineups** from the MLB Stats API game feed, captured pre-game with a timestamp. After the game you know who played, not *when that was known*.
5. **Weather forecasts at decision time** (Open-Meteo is free; no key). Actual weather can be reconstructed, forecasts can't, and forecasts are what the market priced.
6. **Your own predictions, features and model IDs, append-only** (C7). Your system's past beliefs are the only way to audit it.
7. **API health metadata:** credits remaining, response latency, errors, rejected rows. That's how you notice when a feed is degrading.
8. **Odds for the sports you don't model yet** (already doing this; keep it). NBA and NHL odds history is worth more than the credits.
9. **Market IDs as the book changes them** (event ID changes, re-listed games after postponement).

Recoverable later, so no need to hoard: Statcast pitches, final scores, nflverse play-by-play, umpire assignments, transactions.

---

## 12. Things NOT to Build Yet

- NBA or NHL models. Finish one sport end to end (the README's own rule).
- Postgres, Airflow/Prefect/Dagster, Kafka or event-driven pipelines, Docker.
- A feature store or MLflow-style model registry. A `models` table plus a content-hash filename is the whole registry you need.
- Gradient boosting or neural nets. With 11 features and a noise-dominated target, they overfit before they help. Revisit once you have 40+ meaningful features *and* a market-anchored baseline to beat.
- A web application or hosted dashboard. The static HTML dashboard is the right tool.
- Great Expectations or Pandera frameworks. Twenty explicit checks in `monitor.py` do more.
- Additional markets (totals, run lines) until moneyline evaluation is trustworthy. Each also costs credits per pull.
- Automated retraining. Weekly manual retraining with an experiment log is safer until monitoring exists.

---

## 13. Outside-the-Box Ideas

1. **Score every game against the market, not just bets.** Each day compute log loss for the model and for the Pinnacle no-vig close on *all* graded games. That's about 2,400 real-market comparisons a season, vs a few hundred bets, and it's a live, out-of-sample gate 1 for MLB starting now. It's the fastest way to learn whether the model has anything.
2. **Buy the history once.** The Odds API historical endpoint is on all paid plans (from $30/month for 20k credits, $59 for 100k), costs 10 credits per snapshot request, and covers 2020 onward. About 3 seasons × ~186 days × ~5 pre-game snapshots ≈ 2,800 requests ≈ 28k credits, so one month of the $59 plan. That turns MLB gate 1 from "wait a year" into "run it this week", and enables idea 3.
3. **Market-residual modeling** (§6 #5). Features only have to explain the market's mistakes, which is the correct question.
4. **Built-in negative controls.** The same pipeline with random sides, shuffled features, or the model's opposite side runs nightly in shadow. If any control ever passes a gate, the gate is broken. This turns "is my measurement honest?" into an automated check.
5. **A replay harness.** `replay(date)` rebuilds features and predictions for a past date from versioned raw data and asserts they match what was stored. This is the proof that lineage works, and it catches silent revisions (L12).
6. **Champion/challenger in shadow.** Every candidate model writes predictions alongside the champion in the append-only table, and gets promoted only on a paired per-game improvement on *future* games. Cheap once C7 is done.
7. **Measure where the edge lives.** Decompose each game's price path (open, bet time, close) and your model's position against it. If your model agrees with where the line *moves*, you have information and should bet earlier. If it only disagrees with the close, you have noise.
8. **Detect stale lines against Pinnacle.** A soft book priced beyond Pinnacle's fair line by more than the vig is +EV regardless of your model. It's worth logging as its own strategy and its own control, because it separates line-shopping value from model value. Be honest that soft books limit winning accounts quickly.
9. **Plan pulls with the free events endpoint.** Pull ~45 min before each start-time cluster, only for sports with games, from your PC (no GitHub delay). That hits the closing window precisely at roughly the same credit cost.
10. **Per-prediction uncertainty as a first-class output.** Carry the starter's batters faced, window sample sizes and staleness into each prediction, and make NO BET automatic below thresholds. Most bad bets in public-data models come from small samples, not bad math.
11. **Season-phase-aware features.** Explicit shrinkage toward prior-season ratings early in the year, instead of either stale September windows (training) or skipping games (live). April is when markets are softest, so it's the worst month to be blind.
12. **An information-arrival log.** Diff consecutive probable, lineup and odds snapshots to produce events ("starter changed 14:32; line moved 18¢ by 14:40"). This becomes both a feature source (reacting to news faster than soft books) and a research dataset.

---

## 14. Ideal sports_machine Architecture (6-12 months)

```
 SOURCES            RAW (immutable)          CORE (SQLite, append-only facts)           SERVING
 Odds API ──┐       data/raw/<src>/<day>/    games(canonical id, start_utc, local_date,  predict(date) ─► predictions
 MLB Stats ─┼─ingest─► *.json.gz ──parse──►        venue_id, status, game_type)          (append-only, model_id,
 ESPN ──────┤       (versioned by pull ts)   game_xref(feed ids ↔ canonical)               code_sha, feature_row_id)
 Statcast ──┘                                odds(snapshot_id, market last_update)        │
                                             probables_history, lineups_history          ▼
                                             weather_forecasts                           bets(prediction_id,
                                                                                              odds_snapshot_id)
 FEATURES: one as-of function per feature, used by BOTH training and live ──► features(feature_row_id, inputs_through)
 MODELS:   models(model_id=sha256, code_sha, data_through, metrics) ; champion + shadow challengers
 EVAL:     nightly: every game vs Pinnacle fair close; paper bets EV; controls; gate records
 OPS:      prod checkout ≠ dev checkout ; monitor.py → health.jsonl → alerts ; verified backups
```

What stays the same: SQLite, parquet, git-archived cloud pulls, the three gates, the fail-closed engine.

---

## 15. Top 10 Next Actions

1. **Fix game identity:** store `start_time_utc` and local date; match on start time; add `game_xref`; regression test from the SD @ LAD rows (C1).
2. **Fix results:** pull yesterday and today's finals in the local job; status taxonomy; postponement repair; reject 0-0 finals (C2, C3).
3. **Fix the Statcast top-up:** cap at yesterday; re-fetch 3 days; pitch-count check (C4).
4. **Only bet pregame;** link each bet to the snapshot it used (C5).
5. **Make predictions, features and models append-only** with code SHA and model hash (C7).
6. **Score every game vs the Pinnacle fair close,** nightly (§13 #1).
7. **Rebuild gate 2:** fair-close EV, random-side placebo, 3 SE (C6).
8. **Start collecting** raw payloads, market `last_update`, credits, probable history and lineups (§11).
9. **Split into a prod checkout,** add pytest to CI, pin dependencies (C8, §10).
10. **Buy one month of historical odds and run MLB gate 1 against a real market** (§13 #2). Then decide whether the MLB model is worth feature work, or whether the market-residual approach is the path.

---

### What I couldn't verify (needs your database)

Model calibration intercept (§6 #1); actual Statcast truncation (C4); how many past predictions were mismatched (C1); NFL out-of-fold `k`; the 7-of-64 closing-window figure.

### Sources

- [The Odds API v4 documentation](https://the-odds-api.com/liveapi/guides/v4/): historical cost 10 per region per market, snapshots from June 2020 (5-minute from September 2022), `/events` free, market-level `last_update`
- [The Odds API plans](https://the-odds-api.com/): 20K $30, 100K $59; historical on all paid plans
