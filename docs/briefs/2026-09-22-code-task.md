# Task for Claude Code: data-correctness fixes, gate 2 rebuild, lineage

Source: two outside reviews (Review 2 of commits 7af37ef..408cb01, and a full audit at 408cb01, saved as
`docs/audit-2026-09-23.md`). Read `CLAUDE.md` first; its rules apply to everything here.

## How to work

- **Verify each finding before you fix it.** Reproduce it from code or data, then fix it, then prove the fix. If a finding is wrong, say so in one sentence and skip it. The reviewer couldn't see `data/`, so the items tagged [needs DB] are unverified.
- **Work in phases.** Stop at the end of each phase and report: what you verified, what you changed, what was wrong, and the `python audit.py` result. Wait for my OK before starting the next phase.
- **Run `python backup.py` before each phase.**
- **Schema changes:** CLAUDE.md says to ask first. For each one, show me the exact migration and wait for my OK before running it.
- **Don't spend API credits.** Don't touch `archive/`, the API key handling, or weaken any gate. Every change here should make the system stricter or more correct.
- **One commit per fix,** with the evidence in the message.
- **Add a pytest test for every bug fixed** (see Phase 3). Build the test from the concrete example given.
- **End each phase with a plain-English summary** for me. I'm not a professional programmer.

---

## Phase 1: Data correctness (critical; nothing downstream is trustworthy until this is done)

**1.1 Late games are matched to the previous night's odds.**

- `ingest/odds.py` stores `game_date = commence_time[:10]`, which is the UTC date. The MLB Stats API stores the local date.
- Games starting at 8pm ET or later get an odds-feed date one day later. `_odds_for()` (model/predict.py) and `odds_twin()` (bets/log.py) match on (date, away, home), so in a series Wednesday's game gets Tuesday's odds row.
- Evidence in archive/: SD @ LAD odds rows dated 2026-09-23 (commence 2026-09-23T02:11Z, which is Tuesday 10:11pm ET) and 2026-09-24. Wednesday's Stats API game is dated 2026-09-23 and matches Tuesday's prices. Same for AZ @ COL and LAA @ ATH.
- ESPN (`ev["date"][:10]`) is also a UTC date.

Fix:

- Store `start_time_utc` on games (Stats API `gameDate`, ESPN `date`, Odds API `commence_time`) and keep `game_date` = local ET date everywhere.
- Match feeds on (away, home, |start − commence| < 3h). This also resolves doubleheaders by time instead of refusing them.
- Consider a `game_xref(canonical_game_id, feed, feed_game_id, match_method, matched_at)` table so matching happens once and is stored. Propose it; don't build it without my OK.
- Merge `_odds_for` and `odds_twin` into one function.
- [needs DB] Report how many existing predictions or paper bets were matched to the wrong game.

**1.2 Live-season finals never arrive.**

- Across every archived games file, 0 of 34 Stats API games reached 'final'.
- Stats API rows only become final via `mlb.pull_day(today)`. The cloud runs it before night games end and never for yesterday. The local scheduled job (`scheduled_check.bat`) never calls it, and `grade` isn't scheduled.
- `bets/paper.settle()` needs status='final' on the bet's own (Stats API) game_id, so paper bets can't settle. The 2026 training data also freezes at `SEASON_DATES[2026]`.

Fix: the local job pulls finals for yesterday and today (Stats API and ESPN) before settling. Verify with a real day.

**1.3 Postponements become permanent, impossible finals.**

- The archive has `mlb-espn-401817035` (TOR @ BAL, 2026-09-22) recorded final 0-0. ESPN marks postponed games state 'post', and scores.py turns a missing score into 0.
- The Stats API gives postponed games `abstractGameState = "Final"` (detailedState "Postponed"), so mlb.py marks them final too.
- Because final is terminal, the make-up game (same gamePk, new date) can never get its score or date.
- `rest_days_asof()` counts postponed dates as games played.

Fix:

- Map to scheduled | live | final | postponed | suspended | cancelled using Stats API `detailedState` / `codedGameState` and ESPN `status.type.name`.
- Only real completions become final. `game_date` and `start_time_utc` may update until a game is final.
- `quality.game()` rejects a final with NULL scores, and an MLB final at 0-0.
- Write a one-time repair query for existing bad rows. Show it to me before running it.

**1.4 The Statcast top-up freezes partial same-day data.** [verify with DB]

- `topup_statcast()` fetches last_date+1 through today. The 10pm job downloads today's in-progress games, and tomorrow starts at today+1, so night games stay truncated.
- The missing part is the late innings, which is bullpen data. This corrupts live bullpen and offense windows only (train/serve skew).
- Verify: pitches per game for recent night games (a complete game is about 280-300).

Fix: cap the top-up at yesterday, always re-fetch the last 3 days (dedupe keeps the last copy), and add an audit check that flags games with fewer than 200 pitches.

**1.5 The 10pm job places paper bets on games already underway.**

- `place()` never checks first-pitch time. `build_for_date` includes live games (`status != 'final'`). `_odds_for` returns the latest pregame price, which is the same snapshot `settle()` later uses as the close. The result is CLV exactly 0, padding the 50-bet floor.
- The 10pm run also overwrites the 11:30am prediction.

Fix:

- `build_for_date` builds only scheduled games.
- `place()` skips games starting within 10 minutes.
- Each bet stores the `odds_snapshot_id` it used, and code asserts `snapshot.ts ≤ bet.ts < start_time`.

**1.6 Venues for the Rays are wrong.**

- Tampa Bay played 2025 at Steinbrenner Field, but `VENUES` only handles the A's, so Rays 2025 home games got a Tropicana-based park factor.
- Neutral-site games get the home team's park.

Fix: take `venue.id` from the Stats API schedule hydrate and key park factors on it. Report how much the 2025 and 2026 TB factors change.

**1.7 Early-season "30-day" windows reach back to last September in training.**

- `rolling("30D")` followed by a row-level `shift(1)` means a team's first game of a season takes the window ending at its last game of the previous season. `rest_days` on opening day is about 170.
- Live skips these games (its date-based window is empty), so training learns from rows that live never sees. The "early-window games dropped" message suggests they're dropped; outside 2022 they aren't.

Verify [needs DB] how many training rows this affects. Then propose (don't implement yet) either (a) masking windows that cross the off-season plus capping rest_days, or (b) building training features with the same `*_asof` functions live uses, so there is one code path. I lean toward (b).

---

## Phase 2: Measurement and lineage

**2.1 Append-only lineage.**

- `features` and `predictions` use INSERT OR REPLACE, so every rerun erases history.
- `model_version = "ridge-mlb-<trained_through>"` isn't unique.

Proposal (schema change: show me first):

- `models(model_id = sha256 of the joblib file, trained_at, code_sha, data_through, alpha, k, metrics_json)`.
- `features(feature_row_id, game_id, created_at, inputs_through, payload)`, append-only.
- `predictions(prediction_id, game_id, created_at, model_id, code_sha, feature_row_id, proj_margin, home_win_prob)`, append-only.
- `bets.prediction_id` and `bets.odds_snapshot_id`.
- "Latest" becomes a query, not an overwrite.

**Status note (commits 869f4bf..92356b5 landed after the review):** PAPER_CLV_SIGMA is already 3.0, the day and season block bootstraps are done, and the start-hour spread rule and `decompose()` exist. Items below are updated to match. Don't redo what's done.

**2.1b Fix `decompose()`; it measures the wrong thing.**

- It averages **American** odds across books (`line_taken − mean(prices)`). That breaks across the ±100 boundary: taking +104 against books at −105/+100/−102/+104 reports a "premium" of 104.75, when the real advantage is about 2.3% in decimal terms.
- It compares against peers at the **closing** snapshot, not at the snapshot the bet was placed from, so it mixes line movement into "shopping".

Replace it with the decomposition in 2.2, which uses probabilities and the bet-time snapshot.

**2.2 Grade gate 2 on expected value against the fair close.**

- `clv_pct` compares to the same book's close with the vig included, so a bet can beat it and still lose money.
- Also, `place()` picks the best of 4 books and grades against that book's close. Outlier prices move back, which biases CLV upward (archive estimate +0.18 points, 16 games, 90% CI −0.13 to +0.51).

Fix:

- Add `ev_fair_close = decimal_taken × p_fair − 1`, where p_fair is the Pinnacle no-vig closing probability for the side bet (fall back to the consensus no-vig across books if Pinnacle is missing, and record which one was used).
- Keep `clv_pct` for display.
- The closing window rule (≤ 60 min) still applies to the fair close.
- **Split each bet into two parts, using fair (no-vig) probabilities for the side bet:**
  - `shop = decimal_taken × p_fair_at_bet − 1`: how good the price was against the fair line **at the moment of the bet**. This is line-shopping value. It's real money, but it isn't model skill.
  - `info = p_fair_close / p_fair_at_bet − 1`: did the fair line move toward the side the model picked. This is the model-skill signal. It doesn't depend on which book was shopped, so a zero-skill model has an expected value of about 0 here, however good the shopping.
  - Together, `(1+shop)(1+info) = 1 + ev_fair_close`.
- **Gate 2 tests `info`** with the existing rules (3.0 SE, 50+ bets). Record `shop` and `ev_fair_close` next to it for reporting.
- This resolves the shopping-vs-forecasting question directly. You don't need ~340 paired games to separate them.
- `p_fair_at_bet` comes from the Pinnacle (or consensus) snapshot at the same timestamp as the bet's `odds_snapshot_id` (see 1.5).

**2.3 Replace the start-hour spread rule with coverage (the spread rule is too easy to satisfy).**

- `MIN_START_HOUR_SPREAD = 3` counts distinct **UTC** hours. The late West Coast slice alone spans 00:xx (Colorado 8:40pm ET), 01:xx and 02:xx (10:10pm ET) UTC. So the same narrow slice that motivated the rule can pass it, and 48 bets in one hour plus one in each of two others passes too.
- The real issue is that the graded bets aren't a random sample of the bets placed. Measure that directly:
  - `coverage = graded paper bets / settled paper bets` over the same period. Gate 2 fails closed below **75%**.
  - Keep a slate-slot check as a backstop, in ET: day (before 5pm), evening (5-8:59pm), late (9pm or later). No slot may exceed 60% of graded bets, **unless** coverage is 90% or higher. At that point the mix is just the real schedule.
  - Report `info` per slot in the gate record, so a model that only works on late games is visible.
- Say plainly in the gate reason when coverage is the blocker. Until pre-game collection is fixed (a local pull ~45 min before each start cluster), coverage will be around 11% and gate 2 **can't** pass, which is the honest state.

**2.4 Placebo control.**

- Run the same paper pipeline in shadow with a random side (seeded), stored as mode='placebo'.
- Gate 2 can't pass while the placebo would also pass under the same rule. Record the placebo result next to the real one in validation.json.

**2.5 Score every game against the market.**

- New nightly job: for every finished MLB game with a Pinnacle close inside 60 minutes, record the model's and the market's per-game log loss (against the latest prediction made before first pitch).
- Report running totals with a day-block bootstrap CI.
- This doesn't clear gate 1 by itself. Report it only, and show me the design before wiring it into any gate.

**2.6 Gate 1 robustness.**

- Independence is already checked (lag-1 autocorrelation −0.006; day and season bootstraps leave the SE unchanged). This item is about something different: **concentration**.
- MLB's pooled t=2.55 rests on 2024 (without 2024, t=1.16). For REAL_MARKET records, also require the pooled margin to stay positive with each season left out.
- Fix the stale docstring in `_pooled_diff`: the per-season SEs under the current baseline are 2.58, 1.01, 0.61 (it says 0.64 for 2025).

---

## Phase 3: Safety nets and hygiene

**3.1 pytest suite plus CI.**

- Create `tests/` for pure functions with no database: odds conversion, de-vig, quality rules, pooled SE math, record_paper math, feed matching (a C1 fixture from the SD @ LAD rows), status mapping (a C3 fixture with a 0-0 and a Postponed payload), top-up date range (C4), pregame guard (C5), and a random-side placebo helper.
- Add a GitHub Action running pytest on every push (no secrets needed).
- Keep `audit.py` for live-data checks (currently 48).

**3.2 Monitoring for the local job.**

- Add `monitor.py`, run at the end of `scheduled_check.bat`, writing `logs/health.jsonl`. Checks:
  - a started game not final after 12h;
  - paper bets unsettled after 36h;
  - a final with NULL or 0-0;
  - a feed match with |commence − start| > 3h;
  - a bet whose snapshot is after first pitch;
  - Statcast stale by more than 1 day or a game with fewer than 200 pitches;
  - more than 20% of the slate skipped;
  - mean predicted home probability outside 50-56%;
  - credits below 25% / 10%.
- Levels: CRITICAL, ERROR, WARNING, INFO. Propose how ERROR or above reaches me (email via Gmail app password, or ntfy.sh). Don't set it up without asking.

**3.3 Guardrails and exposure.**

- No caller passes flags to `evaluate()`, so no guardrail can fire. Compute `sp_unconfirmed` (probable changed or unconfirmed near start) and `opener_flag` (the probable averaged fewer than 2 innings over recent appearances), and pass them in `place()` and `picks()`.
- Enforce `MAX_DAILY_PCT`, which is defined but unused.

**3.4 Dependencies.**

- Add `pyarrow` (every parquet read or write needs it). Remove `nfl_data_py` and `nba_api` (unused).
- Pin versions. CI uses Python 3.11, local uses 3.14: tell me which to standardize on.

**3.5 Collect what can't be recovered later** (write-only additions, no behavior change):

- store the Odds API market-level `last_update`;
- log `x-requests-remaining` / `x-requests-used` per pull;
- save raw API responses gzipped under `data/raw/<source>/<date>/`;
- keep a timestamped probable-pitcher history (every pull, not just the latest non-null);
- strip query strings (the API key) from printed HTTP errors.

**3.6 Check the model's home-team calibration** [needs DB, report only].

- `p = sigmoid(k·margin)` has no intercept. MLB home teams win 52-55% but average only about +0.1-0.2 runs, so predictions may under-rate home teams.
- Report the mean predicted home probability vs the actual rate per test season, plus a reliability table (`calibrate.reliability_table` exists but is never called).
- If it's off, propose an out-of-fold `sigmoid(a + k·margin)` fit. Don't change the model without asking.

---

## Phase 4: Separate production folder

Right now the scheduled jobs run `git pull --autostash` and then execute code in the same folder I edit in. A half-finished edit can run unattended at 11:30am, and a pull conflict can leave the folder mid-rebase while I'm working.

1. Make every data path come from one place (`paths.py` or `config.py`) that honors an environment variable such as `SPORTS_MACHINE_DATA_DIR`. It defaults to `./data`, so nothing changes for anyone who doesn't set it. Check backup.py, backfill*.py, db.py, model/persist.py, features/*, dashboard.py and audit.py.
2. Write `docs/production-setup.md`: step-by-step instructions (in plain English, with exact PowerShell commands) for:
   - cloning a second copy to `C:\sports-machine-prod`;
   - moving `data/` to a location both copies can use, or keeping it with prod and pointing dev at it read-only;
   - repointing the Windows scheduled task at the prod copy's `scheduled_check.bat`;
   - how updates flow (dev edits → `audit.py` green → push → prod pulls only `main`, never with local changes).
3. Make `scheduled_check.bat` refuse to run if its folder has uncommitted changes, and log that it refused.
4. Don't move my data or change the scheduled task yourself. I'll follow the doc. Tell me what to check afterward to confirm it worked.

## Phase 5: Historical odds backfill (a one-time paid pull, only after Phase 1 is done)

Goal: test the MLB model against a REAL market for past seasons (gate 1), instead of waiting a year to collect odds.

Background:

- The Odds API historical endpoint is on all paid plans (20K credits for $30/month, 100K for $59).
- It costs 10 credits per request (1 market, 1 region or up to 10 bookmakers).
- Snapshots go back to June 2020, at 5-minute intervals since September 2022.
- One request returns every game in that snapshot.

Do this in order and stop after step 3 for my approval:

1. **Design (no API calls):** a script `backfill_odds_history.py` that, for each MLB date in 2022-2025 (and 2026 to date), requests snapshots ~45 minutes before each first-pitch cluster that day, using the start times from Phase 1. Also request one morning "open" snapshot per day. Books: pinnacle, draftkings, fanduel, betmgm. Market: h2h only.
2. **Cost estimate:** count the exact requests and credits it would make, per season and in total. The target is under 30k credits (fits one month of the $59 plan with room to spare). If it's over, propose where to thin it (e.g. drop the open snapshot, or fewer seasons).
3. **Safety design:**
   - a `--max-credits` cap it can't exceed (it reads `x-requests-remaining` after every call and stops);
   - `--dry-run` as the default;
   - resumable (skips snapshots already stored);
   - stores raw gzipped responses under `data/raw/odds_history/`;
   - writes into `odds_snapshots` with `snapshot_type='hist_open'` / `'hist_close'` so it can never mix with live pulls;
   - matches games using the Phase 1 start-time matching, never date strings.
   Show me the plan and the dry-run output. **I will buy the plan and give the go-ahead myself. Do not run it without my explicit OK.**
4. After the pull: rebuild the MLB training table with the de-vigged Pinnacle close as `novig_home_prob` (fall back to consensus, and record which), run the walk-forward, and `record()` it as REAL_MARKET. Report per-season and pooled results, the leave-one-season-out check, and a reliability table. Also report model vs the OPENING price, since that's where a bettor would actually get in.
5. Tell me plainly whether the MLB model beats the market, and by how much relative to the noise. Don't tune anything to make it pass. If it loses, say so.

## Not in scope

Don't start these; just note if they come up: NBA/NHL models, new markets, changing the model type, changing the cloud cron schedule.
