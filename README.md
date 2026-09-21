# The Machine — Multi-Sport Betting Model

Ridge-on-margin models (one per sport) with walk-forward validation,
per-sport calibration, quarter-Kelly staking, and CLV-first tracking.
Sports on by default: MLB, NFL, NBA, NHL (config.py; NCAA stubs off).

## Setup (once)
1. `pip install -r requirements.txt`
2. `python db.py`
3. Key from the-odds-api.com -> `export ODDS_API_KEY=...`
   (4 sports x 2 pulls/day fits the $30/mo tier; free tier for testing)
4. Optional: GitHub repo + ODDS_API_KEY secret -> Actions runs it daily.
   Scheduled workflows need a public repo or GitHub Pro on private.

## Daily use
- `python run_daily.py morning` - schedules + odds + features (in-season sports only)
- `python run_daily.py close`   - closing lines (CLV anchor)
- `python run_daily.py grade`   - finals + review (kill criterion enforced)

## Architecture
- config.py            sport registry: odds keys, ESPN paths, per-sport k + edge thresholds
- ingest/odds.py       The Odds API, loops active sports
- ingest/scores.py     ESPN scoreboard (keyless) - schedules/finals all sports
- ingest/mlb.py        MLB Stats API - probable pitchers
- features/sports/     per-sport feature contracts (wire real data at TODOs):
    mlb: bullpen quality/fatigue, team-aggregate offense, SP tails
    nfl: rolling EPA/play (nfl_data_py), QB status, rest, weather
    nba: net rating, back-to-backs, star availability, travel
    nhl: 5v5 xG share, goalie GSAx + confirmation (MoneyPuck/NHL API)
- model/train.py       generic ridge-on-margin + walk-forward CV per sport
- model/calibrate.py   fit margin->win-prob k per sport, reliability, Brier
- bets/engine.py       no-vig, per-sport min edge (NFL/NBA 4%, MLB/NHL 3.5%),
                       quarter Kelly, 3% cap, per-sport guardrails
                       (SP/QB/goalie unconfirmed, openers, star scratches)
- bets/log.py          bet log + CLV grading + kill criterion (neg CLV over 50 -> stop)

## Build order per sport (do not skip ahead)
1. Backfill 3-5 seasons; wire the sport's feature module.
2. Walk-forward CV must beat the no-vig market baseline out of sample.
3. Calibrate. 4. Paper-trade 50+ picks to positive CLV. 5. Then money.
Validate ONE sport end-to-end (start MLB) before wiring the next -
four half-validated models are worse than one proven one.
