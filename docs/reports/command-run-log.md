# Command run log

Every command, in the order a scheduled day runs them, against `data_phase1/` - a copy taken from a verified backup. The live database is not touched.

| # | command | exit | seconds | first line of output |
|---|---|---|---|---|
| 1 | `morning` | **skipped** | — | costs 1 credit per in-season sport |
| 2 | `run_daily.py predict` | 0 | 23.2 | [2026] statcast top-up: 2026-09-20 .. 2026-09-23 |
| 3 | `run_daily.py paper` | 0 | 0.2 | Paper trading [mlb] |
| 4 | `close` | **skipped** | — | costs 1 credit per in-season sport |
| 5 | `run_daily.py finals` | 0 | 0.7 | Results for 2026-09-23: |
| 6 | `run_daily.py grade` | 0 | 0.7 | Results for 2026-09-23: |
| 7 | `run_daily.py market` | 0 | 0.3 | Market scoring [mlb]: +2 newly scored (10 without a usable close, 372 without a pre-game pre |
| 8 | `run_daily.py picks` | 0 | 1.3 | matchup                                    model  market    edge  verdict |
| 9 | `monitor.py` | 0 | 1.1 | [INFO    ] games reach a final state: 0 game(s) started over 12h ago and are still not final |
| 10 | `cronstatus.py` | 0 | 1.9 | CRON STATUS                              now Thu 12:25 AM ET |
| 11 | `healthcheck.py` | 0 | 0.1 | Healthcheck: all green (mlb, nfl). |
| 12 | `merge_archive.py` | 0 | 1.4 | Merged 2 new file(s) of 22: +0 snapshots, 19579 game upserts. Local archive now holds 281688 |
| 13 | `export_snapshots.py` | 0 | 0.9 | Exported 281688 snapshots, 19579 games -> odds-2026-09-24-0425.csv, games-2026-09-24-0425.cs |
| 14 | `backup.py` | 0 | 0.7 | Backed up to C:\Users\BromC\OneDrive\sports-machine-backups\machine-2026-09-24-0025.db  (91. |
| 15 | `dashboard.py` | 0 | 1.7 | Wrote C:\Users\BromC\sports-machine-dev\dashboard.html |
| 16 | `db.py` | 0 | 0.1 | DB initialized at C:\Users\BromC\sports-machine-dev\data_phase1\machine.db |
| 17 | `db.py` | 0 | 0.1 | DB initialized at C:\Users\BromC\sports-machine-dev\data_phase1\machine.db |
| 18 | `model/validation.py` | 0 | 0.0 | mlb: NOT CLEARED - walk-forward FAIL (lost to market in 3 of 3 seasons (2024, 2025, 2026));  |
| 19 | `props/collect.py --plan` | 0 | 0.6 | ========================================================================== |
| 20 | `research/tools/integration_matrix.py --check` | 0 | 0.4 | 29 entry points, 0 undocumented |
| 21 | `audit.py` | 0 | 20.6 | ============================================================================== |

Every command that was run exited **0**.
