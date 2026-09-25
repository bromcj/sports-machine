"""SQLite storage layer. Single source of truth for the machine.
Every table carries a `sport` column — one DB, all leagues.
"""
import datetime as dt
import sqlite3
from pathlib import Path

import paths

# Default from paths.py. Stays a module-level name so audit.py and the
# tests can reassign it to a temp file.
DB_PATH = paths.DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    game_id TEXT PRIMARY KEY,
    sport TEXT NOT NULL,
    game_date TEXT NOT NULL,       -- LOCAL (ET) calendar date, never UTC
    start_time_utc TEXT,           -- true first pitch; what feeds are matched on
    venue_id TEXT,                 -- MLB Stats API venue.id, for park factors
    away TEXT NOT NULL,
    home TEXT NOT NULL,
    away_starter TEXT,             -- SP (MLB) / QB or goalie if tracked
    home_starter TEXT,
    away_score INTEGER,
    home_score INTEGER,
    status TEXT DEFAULT 'scheduled'
);

CREATE TABLE IF NOT EXISTS odds_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    sport TEXT NOT NULL,
    ts TEXT NOT NULL,
    book TEXT NOT NULL,
    away_ml INTEGER,
    home_ml INTEGER,
    snapshot_type TEXT NOT NULL,   -- 'open' | 'close' (live pulls) |
                                   -- 'hist_open' | 'hist_close' (bought history)
    commence_time TEXT             -- ISO first-pitch time, from the odds API.
                                   -- snapshot_type records WHICH PULL this came
                                   -- from; this records how close to the actual
                                   -- start it was. Only the second one lets you
                                   -- identify a true closing line per game.
);

-- LINEAGE: features, predictions and models are APPEND-ONLY.
--
-- They used to be INSERT OR REPLACE keyed on game_id, so every rerun erased
-- the previous one. The 11:30am prediction was overwritten by the 10pm run,
-- and model_version ("ridge-mlb-<date>") was identical for two different fits
-- on the same data. When a result looked too good or too bad there was no way
-- to answer "what did the system know at the time?" - it could not even say
-- which of the day's two predictions a paper bet came from.
--
-- "Latest" is now a query, not an overwrite.
CREATE TABLE IF NOT EXISTS features (
    feature_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    sport TEXT NOT NULL,
    created_at TEXT NOT NULL,
    inputs_through TEXT,           -- how current the Statcast file was
    payload TEXT NOT NULL          -- JSON feature vector
);

CREATE TABLE IF NOT EXISTS models (
    model_id TEXT PRIMARY KEY,     -- sha256 of the joblib file: two fits on the
                                   -- same data are two different models
    sport TEXT NOT NULL,
    trained_at TEXT NOT NULL,
    code_sha TEXT,                 -- git commit the fit ran from
    data_through TEXT,
    alpha REAL,
    k REAL,
    n_train_rows INTEGER,
    metrics_json TEXT
);

CREATE TABLE IF NOT EXISTS predictions (
    prediction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    sport TEXT NOT NULL,
    created_at TEXT NOT NULL,
    model_id TEXT,
    code_sha TEXT,
    feature_row_id INTEGER,
    model_version TEXT NOT NULL,   -- kept for display; model_id is the identity
    proj_margin REAL NOT NULL,     -- home minus away, sport-native units
    home_win_prob REAL NOT NULL
);

-- Every finished game scored against the market, whether or not a bet was
-- ever placed on it. Gate 1 is judged on the bought closes in market_close;
-- this is the running head-to-head for games as they finish, using the
-- prediction actually made before first pitch. It clears nothing by itself.
CREATE TABLE IF NOT EXISTS market_scores (
    game_id TEXT PRIMARY KEY,
    sport TEXT NOT NULL,
    game_date TEXT NOT NULL,
    scored_at TEXT NOT NULL,
    prediction_id INTEGER,         -- the latest prediction made BEFORE first pitch
    model_prob REAL NOT NULL,
    market_prob REAL NOT NULL,     -- de-vigged close, inside the window
    fair_source TEXT,
    home_won INTEGER NOT NULL,
    model_ll REAL NOT NULL,
    market_ll REAL NOT NULL
);

-- Write-only history. None of this can be reconstructed later, so it is
-- collected now even though nothing reads most of it yet.
CREATE TABLE IF NOT EXISTS api_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    sport TEXT,
    endpoint TEXT,
    remaining INTEGER,             -- x-requests-remaining
    used INTEGER                   -- x-requests-used
);

-- Every probable ever announced, not just the latest non-null. A late scratch
-- is invisible once the row is overwritten, and "was the starter confirmed
-- when we bet?" is unanswerable after the fact.
CREATE TABLE IF NOT EXISTS probables_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    away_starter TEXT,
    away_starter_id INTEGER,
    home_starter TEXT,
    home_starter_id INTEGER
);

-- Which historical snapshots have been paid for and stored. This is a ONE-TIME
-- purchase at 10 credits a request, so a rerun must never buy the same
-- snapshot twice - and a failed request must be recorded too, or a retry loop
-- could quietly spend the budget on something that will never succeed.
CREATE TABLE IF NOT EXISTS odds_history_progress (
    snapshot_ts TEXT PRIMARY KEY,
    kind TEXT NOT NULL,            -- hist_open | hist_close
    fetched_at TEXT NOT NULL,
    n_prices INTEGER,
    credits_remaining INTEGER,
    ok INTEGER NOT NULL DEFAULT 1,
    note TEXT
);

-- The de-vigged closing probability for each finished game, resolved once.
-- Building it is a per-game walk over snapshots, which is far too slow to
-- repeat inside every training run - and materialising it also means the
-- number gate 1 is judged on can be inspected directly rather than recomputed
-- differently by each reader.
CREATE TABLE IF NOT EXISTS market_close (
    game_id TEXT PRIMARY KEY,      -- the Stats API game
    odds_game_id TEXT NOT NULL,
    snapshot_ts TEXT NOT NULL,
    minutes_before_start REAL,
    p_fair_home REAL NOT NULL,     -- de-vigged, Pinnacle where available
    source TEXT NOT NULL,          -- pinnacle | consensus
    resolved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS merged_files (
    name TEXT PRIMARY KEY,         -- archive/ filename, e.g. odds-2026-09-23-0053.csv
    bytes INTEGER NOT NULL,        -- size when merged; a changed size means re-read
    rows INTEGER NOT NULL,
    merged_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bets (
    bet_id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL DEFAULT 'real',   -- paper|real|placebo|manual; gate 2 counts paper
    ts TEXT NOT NULL,
    game_id TEXT NOT NULL,
    sport TEXT NOT NULL,
    side TEXT NOT NULL,
    book TEXT NOT NULL,
    line_taken INTEGER NOT NULL,
    stake REAL NOT NULL,
    model_prob REAL NOT NULL,
    novig_market_prob REAL NOT NULL,
    edge REAL NOT NULL,
    kelly_fraction REAL NOT NULL,
    odds_snapshot_id INTEGER,      -- the exact price row this bet was taken at
    closing_line INTEGER,
    novig_closing_prob REAL,
    clv_pct REAL,
    result TEXT,
    pnl REAL,
    model_version TEXT
);

-- ------------------------------------------------------------- scanner ---
-- Every venue in one shape: sportsbooks (via The Odds API), Kalshi and
-- Polymarket. See docs/venues.md. Added 2026-09-25 as new tables only; no
-- existing table or column is touched.

-- A market is one venue's contract on one question. A sportsbook's spread at
-- -3.5 and the same book's spread at -3 are two markets, because they are two
-- different bets. market_id is ours, built from the other columns
-- (scanner.store.market_key), so re-polling finds the same row.
CREATE TABLE IF NOT EXISTS markets (
    market_id TEXT PRIMARY KEY,
    venue TEXT NOT NULL,               -- 'sportsbook:<book>' | 'kalshi' | 'polymarket'
    venue_market_id TEXT NOT NULL,     -- the venue's id: odds event id, Kalshi ticker, condition id
    sport TEXT,                        -- NULL for a non-game market
    canonical_event_id TEXT NOT NULL,  -- a games.game_id, matched through feeds; or a
                                       -- non-game key of its own ('weather:KNYC:2026-10-01')
    market_type TEXT NOT NULL,         -- h2h | spread | total | prop | futures | binary
    line REAL,                         -- the home side's spread, or the total; NULL otherwise
    yes_outcome TEXT,                  -- a yes/no contract on a game: which canonical
                                       -- outcome YES is ('home', 'over'...). NULL otherwise
    event_start TEXT,                  -- first pitch / tip-off, aware UTC; NULL if none
    resolves_at TEXT,                  -- when it settles, for capital lock-up
    resolution_source TEXT,
    first_seen TEXT NOT NULL
);

-- APPEND-ONLY, like archive/. One row is one price on offer at one moment.
-- `price` is always a probability: 1/decimal for a book (the vig still in it -
-- scanner.fair takes it out), the contract price in dollars for an exchange.
CREATE TABLE IF NOT EXISTS prices (
    price_id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    venue TEXT NOT NULL,
    outcome TEXT NOT NULL,             -- home | away | over | under | yes | no
    quote TEXT NOT NULL,               -- 'ask': what buying this outcome costs;
                                       -- 'bid': what selling it fetches. Books: ask only
    level INTEGER NOT NULL DEFAULT 1,  -- 1 is the best price; order books keep three
    price REAL NOT NULL,
    price_native TEXT NOT NULL,        -- as received: '-110', '0.4500'
    size_available REAL,               -- contracts at this price; NULL for a book
    fee_model TEXT NOT NULL,           -- scanner.fees key: how EV after fees is computed
    captured_at TEXT NOT NULL,         -- when WE saw it, aware UTC
    source_last_update TEXT,           -- when the VENUE last moved it, if it says
    raw_ref TEXT                       -- where the raw payload was saved
);

-- Every call to a metered API (only The Odds API is). The brief's credit cap
-- and the monthly budget are sums over this table, checked BEFORE each call.
CREATE TABLE IF NOT EXISTS credit_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    consumer TEXT NOT NULL,            -- which code spent it: 'poll'
    sport TEXT,
    endpoint TEXT NOT NULL,
    estimated INTEGER NOT NULL,        -- the cost checked against the caps beforehand
    cost INTEGER,                      -- x-requests-last; NULL if no response came back,
                                       -- in which case the estimate counts
    remaining INTEGER,                 -- x-requests-remaining afterwards
    used INTEGER,                      -- x-requests-used afterwards
    ok INTEGER NOT NULL,
    note TEXT
);

-- Paper execution (scanner/paper.py). The CHECK is the database refusing any
-- order that is not paper: there is no 'real' mode for a row to be in.
CREATE TABLE IF NOT EXISTS paper_orders (
    order_id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('paper', 'placebo')),
    venue TEXT NOT NULL,
    market_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('taker', 'maker')),
    size REAL NOT NULL,                -- contracts; for a book, dollars staked
    limit_price REAL NOT NULL,         -- a probability; never fills worse than this
    placed_at TEXT NOT NULL,
    expires_at TEXT,                   -- a maker order rests until then
    exposure REAL NOT NULL,            -- the most it can lose, fees included; the
                                       -- daily cap is a sum of this
    fair_p REAL,
    fair_source TEXT,
    fair_as_of TEXT,
    ev_at_order REAL,                  -- per dollar, after fees, at the limit
    status TEXT NOT NULL DEFAULT 'open',   -- open | partial | filled | expired
    note TEXT
);

-- One row per simulated fill, against a price observed AFTER the order.
CREATE TABLE IF NOT EXISTS paper_fills (
    fill_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    price_id INTEGER NOT NULL,
    filled_at TEXT NOT NULL,           -- that price's captured_at
    contracts REAL NOT NULL,
    price REAL NOT NULL,
    fee REAL NOT NULL                  -- dollars
);

-- What an order became once something filled. Capital accounting lives here:
-- ev is the return per dollar locked up, and annualized_ev says how long it is
-- locked up for. Result and grading columns are filled at settlement.
CREATE TABLE IF NOT EXISTS paper_positions (
    position_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL UNIQUE,
    strategy TEXT NOT NULL,
    mode TEXT NOT NULL,
    venue TEXT NOT NULL,
    market_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    contracts REAL NOT NULL,
    avg_price REAL NOT NULL,
    fee REAL NOT NULL,                 -- dollars
    stake REAL NOT NULL,               -- capital locked: contracts x avg_price + fee
    opened_at TEXT NOT NULL,
    resolves_at TEXT,
    days_to_resolution REAL,
    fair_p REAL,
    fair_source TEXT,
    ev REAL,                           -- expected return per dollar staked, after fees
    annualized_ev REAL,                -- ev / days_to_resolution x 365
    result TEXT,                       -- win | loss | push | void
    pnl REAL,
    settled_at TEXT,
    fair_close_p REAL,
    fair_close_source TEXT,
    info REAL,
    realized_ev REAL,
    graded_at TEXT
);

-- Every time a strategy's gate 2 is re-tested. Re-testing is optional
-- stopping; the number of looks is what sets the false-pass rate, so it is
-- recorded rather than assumed (docs/gates.md).
CREATE TABLE IF NOT EXISTS gate_looks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    looked_at TEXT NOT NULL,
    metric TEXT NOT NULL,
    n INTEGER NOT NULL,
    mean REAL,
    se REAL,
    t REAL,
    coverage REAL,
    passed INTEGER NOT NULL,
    reason TEXT
);
"""


# "Latest" for an append-only table. One definition, so no reader invents its
# own and they cannot disagree about which prediction a bet acted on.
LATEST_FEATURE = """
    SELECT f.* FROM features f
    JOIN (SELECT game_id, MAX(feature_row_id) AS m FROM features GROUP BY game_id) x
      ON x.game_id = f.game_id AND x.m = f.feature_row_id
"""
LATEST_PREDICTION = """
    SELECT p.* FROM predictions p
    JOIN (SELECT game_id, MAX(prediction_id) AS m FROM predictions GROUP BY game_id) x
      ON x.game_id = p.game_id AND x.m = p.prediction_id
"""


def code_sha() -> str | None:
    """The git commit this is running from, for prediction lineage."""
    import subprocess
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=10,
                           cwd=Path(__file__).parent)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except Exception:
        return None


def utc_now() -> str:
    """The timestamp format that goes into this database.

    Timezone-AWARE UTC, e.g. 2026-09-23T03:38:43+00:00.

    Before this, odds_snapshots.ts and bets.ts were written with
    datetime.utcnow() - naive, no offset - while predictions.ts and
    features.asof_ts used now(timezone.utc) and carried one. Comparing a naive
    datetime with an aware one raises TypeError, so every read site that
    touched both had to normalise defensively, and bets/log.py still carries a
    four-line comment explaining the trap. The site that forgot would not be a
    wrong answer, it would be a crash.

    Fixing it at the write side means there is nothing to remember at the read
    side. utcnow() is also deprecated and scheduled for removal.
    """
    return dt.datetime.now(dt.timezone.utc).isoformat()


def as_utc(value: str | None) -> str | None:
    """Normalise any stored timestamp to aware UTC ISO. Idempotent.

    Accepts naive (assumed UTC, which is what utcnow() produced), 'Z'-suffixed,
    and already-offset strings. Anything unparseable is returned untouched
    rather than discarded - a weird timestamp is a thing to investigate, not to
    silently drop.
    """
    if not value:
        return value
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        when = dt.datetime.fromisoformat(text)
    except ValueError:
        return value
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return when.astimezone(dt.timezone.utc).isoformat()


# Columns holding a timestamp this project writes. Rows predating utc_now()
# are naive; normalize_timestamps() rewrites them once so the column holds one
# format. commence_time is NOT here - that is the odds API's value, already
# 'Z'-suffixed, and normalising it would rewrite upstream data.
TS_COLUMNS = [("odds_snapshots", "id", "ts"), ("bets", "bet_id", "ts")]


def _rebuild_append_only(con):
    """Move features/predictions from game_id-keyed to append-only. Idempotent.

    SQLite cannot drop a PRIMARY KEY, so the table is rebuilt and the existing
    rows copied into it. Both tables are small and regenerable, so this is
    cheap - but it still copies rather than discarding, because throwing away
    the only record of what the system predicted is the opposite of the point.
    """
    done = []
    cols = {r["name"] for r in con.execute("PRAGMA table_info(features)")}
    if cols and "feature_row_id" not in cols:
        con.execute("ALTER TABLE features RENAME TO features_old")
        con.executescript(SCHEMA)
        con.execute(
            "INSERT INTO features (game_id, sport, created_at, payload)"
            " SELECT game_id, sport, asof_ts, payload FROM features_old")
        n = con.execute("SELECT COUNT(*) FROM features_old").fetchone()[0]
        con.execute("DROP TABLE features_old")
        done.append(f"features ({n} rows carried over)")

    cols = {r["name"] for r in con.execute("PRAGMA table_info(predictions)")}
    if cols and "prediction_id" not in cols:
        con.execute("ALTER TABLE predictions RENAME TO predictions_old")
        con.executescript(SCHEMA)
        con.execute(
            "INSERT INTO predictions (game_id, sport, created_at, model_version,"
            " proj_margin, home_win_prob)"
            " SELECT game_id, sport, ts, model_version, proj_margin,"
            " home_win_prob FROM predictions_old")
        n = con.execute("SELECT COUNT(*) FROM predictions_old").fetchone()[0]
        con.execute("DROP TABLE predictions_old")
        done.append(f"predictions ({n} rows carried over)")
    return done


def normalize_timestamps(con):
    """One-time rewrite of naive timestamps to aware UTC. Idempotent."""
    fixed = []
    for table, key, col in TS_COLUMNS:
        cols = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            continue
        rows = con.execute(
            f"SELECT {key} k, {col} v FROM {table}"
            f" WHERE {col} IS NOT NULL AND {col} NOT LIKE '%+00:00'").fetchall()
        n = 0
        for r in rows:
            new = as_utc(r["v"])
            if new == r["v"]:
                continue
            try:
                con.execute(f"UPDATE {table} SET {col}=? WHERE {key}=?",
                            (new, r["k"]))
            except sqlite3.IntegrityError as e:
                raise SystemExit(
                    f"Normalising {table}.{col} on row {r['k']} collides with an "
                    f"existing row: {e}\n"
                    "That means the same observation is stored twice in two "
                    "timestamp formats. Inspect before rerunning.")
            n += 1
        if n:
            fixed.append(f"{table}.{col} x{n}")
    return fixed


def normalize_game_dates(con):
    """game_date := the LOCAL date of first pitch, wherever we know it.

    Self-healing rather than a one-off. The upsert freezes game_date once a
    game is final, so an OLD row that gains a start_time_utc for the first
    time - which happens whenever a feed starts supplying one, or a backfill
    fills it in - keeps whatever date it was written with. That date may be a
    UTC one from before the fix. This puts it right on the next `python db.py`.
    """
    from feeds import et_date
    fixed = 0
    for r in con.execute(
            "SELECT game_id, game_date, start_time_utc FROM games"
            " WHERE start_time_utc IS NOT NULL").fetchall():
        want = et_date(r["start_time_utc"])
        if want and want != r["game_date"]:
            con.execute("UPDATE games SET game_date=? WHERE game_id=?",
                        (want, r["game_id"]))
            fixed += 1
    return fixed


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


# Columns added after the first release. Each is applied to an existing DB if
# missing, so `python db.py` is safe to re-run and upgrades in place.
MIGRATIONS = [
    ("odds_snapshots", "commence_time", "TEXT"),
    # The Odds API's own market-level timestamp: when the BOOK last moved this
    # price, as opposed to when we happened to pull it. Without it a price
    # that has not moved for six hours is indistinguishable from a fresh one.
    ("odds_snapshots", "market_last_update", "TEXT"),
    # MLBAM player ids for the probable starters. The names alone cannot be
    # joined to Statcast, which keys pitchers by id - so without these there is
    # no way to look up a probable starter's recent form before a game.
    ("games", "away_starter_id", "INTEGER"),
    ("games", "home_starter_id", "INTEGER"),
    # 'paper' | 'real' | 'placebo' (the same pipeline on a random side, gate
    # 2's control) | 'manual' (your own bets, Phase 2 - bets/manual.py).
    # Gate 2 counts PAPER bets only - it is the evidence
    # gathered BEFORE any money is staked, so letting real wagers feed it
    # would let money already at risk justify risking more. NULL on rows
    # written before this column existed is read as 'real', which is the
    # fail-closed direction: an unlabelled bet cannot help pass a gate.
    ("bets", "mode", "TEXT"),
    # The true first-pitch instant, from whichever feed minted the row.
    # game_date alone cannot identify a game: the odds API and ESPN both date
    # rows by UTC while the MLB Stats API dates them locally, so every game
    # starting after 8pm ET carries a date one day ahead. Matching on
    # (date, away, home) then hands a late game the PREVIOUS night's prices.
    ("games", "start_time_utc", "TEXT"),
    # MLB Stats API venue.id. VENUES was a hand-maintained map that knew only
    # about the Athletics, so Tampa Bay's 2025 home games at Steinbrenner Field
    # were given Tropicana's park factor.
    ("games", "venue_id", "TEXT"),
    # Exactly which price a bet was taken at, so "was this bet placeable?" is
    # answerable later rather than inferred.
    ("bets", "odds_snapshot_id", "INTEGER"),
    # Gate 2's real measurements. clv_pct compares to the same book's close
    # WITH the vig in it, so a bet can beat it and still lose money, and it
    # cannot separate a good price from a good forecast.
    #   ev_fair_close  decimal_taken * p_fair_close - 1   (the money)
    #   shop_pct       decimal_taken * p_fair_at_bet - 1  (line shopping)
    #   info_pct       p_fair_close / p_fair_at_bet - 1   (model skill)
    # (1+shop)(1+info) = 1+ev. Gate 2 tests info, because shop is real money
    # but is not evidence the model forecasts anything.
    ("bets", "ev_fair_close", "REAL"),
    ("bets", "shop_pct", "REAL"),
    ("bets", "info_pct", "REAL"),
    ("bets", "fair_source", "TEXT"),        # 'pinnacle' | 'consensus'
    # Which prediction this bet acted on. Two predictions a day were possible
    # and nothing recorded which one a bet came from.
    ("bets", "prediction_id", "INTEGER"),

    # ---- mode='manual', so a human's own bets are graded on exactly
    # the terms the model's are. Every column here is additive and NULL on
    # every existing row, so nothing already recorded changes meaning.
    #
    # 'h2h' | 'spreads' | 'totals' | a prop market key. NULL reads as 'h2h',
    # which is what every row written before this column was.
    ("bets", "market", "TEXT"),
    # The player a prop is on. NULL for game-level markets.
    ("bets", "player", "TEXT"),
    # The prop or handicap NUMBER (5.5 receptions, -3.5 points). Distinct from
    # line_taken, which is the PRICE (-110). Conflating the two is how a
    # scoreboard ends up grading "over 110".
    ("bets", "prop_line", "REAL"),
    # boost | promo | research | sgp-leg. The question the scoreboard answers
    # is "is this handicapping worth continuing", and that is only answerable
    # per tag: a boosted price and a researched pick are not the same bet.
    ("bets", "tag", "TEXT"),
    # For markets whose result we do not hold (most props). Entered by hand and
    # FLAGGED as such, so a typed outcome can never be mistaken for one settled
    # from a box score.
    ("bets", "manual_result", "TEXT"),
    # What was available elsewhere at the moment of the bet, and what the fair
    # price was then. Without these the shop/info split cannot be computed for
    # a human's pick: shop is the price taken against fair-at-bet, info is
    # fair-at-bet against fair-at-close. The model's bets get both from the
    # snapshot they were placed against; a manual bet has no snapshot.
    ("bets", "best_available", "INTEGER"),
    ("bets", "p_fair_at_bet", "REAL"),
]


# Indexes. Created by init(), so `python db.py` adds them to an existing DB.
#
# Without these EVERY hot query is a full table scan - measured with EXPLAIN
# QUERY PLAN on the live DB, all five of them. That is invisible at a thousand
# rows and fatal at a hundred thousand, because merge_archive.py does one
# dedupe probe per archived row against a table growing at the same rate. That
# is O(n^2): measured, a year of collection takes the daily merge from instant
# to 84 minutes. With the first index below it is 7 seconds.
#
# The first one is UNIQUE, which also turns deduplication from a convention
# enforced in one hand-written SELECT inside merge_archive.py into something
# the database will not let any writer violate.
INDEXES = [
    # (game_id, ts, book, snapshot_type) identifies one price from one book in
    # one pull. A second row with that key is the same observation twice.
    ("ux_snap_dedupe", "CREATE UNIQUE INDEX IF NOT EXISTS ux_snap_dedupe"
                       " ON odds_snapshots(game_id, ts, book, snapshot_type)"),
    # healthcheck counts per sport; 'latest snapshot for X' sorts by ts.
    ("ix_snap_sport_ts", "CREATE INDEX IF NOT EXISTS ix_snap_sport_ts"
                         " ON odds_snapshots(sport, ts)"),
    # odds_twin() and every 'today's slate' query filter on exactly this pair.
    ("ix_games_sport_date", "CREATE INDEX IF NOT EXISTS ix_games_sport_date"
                            " ON games(sport, game_date)"),
    # settle() sweeps ungraded paper bets every run; gate 2 reads graded ones.
    ("ix_bets_mode_sport", "CREATE INDEX IF NOT EXISTS ix_bets_mode_sport"
                           " ON bets(mode, sport, result)"),
    # "the latest row for this game" is now a query, so it needs an index.
    ("ix_pred_game_created", "CREATE INDEX IF NOT EXISTS ix_pred_game_created"
                             " ON predictions(game_id, created_at)"),
    ("ix_feat_game_created", "CREATE INDEX IF NOT EXISTS ix_feat_game_created"
                             " ON features(game_id, created_at)"),
    ("ix_prob_game_seen", "CREATE INDEX IF NOT EXISTS ix_prob_game_seen"
                          " ON probables_history(game_id, seen_at)"),
    # Only one row per (game, pull) is worth keeping: the same probable seen
    # again is not new information.
    ("ux_prob_dedupe", "CREATE UNIQUE INDEX IF NOT EXISTS ux_prob_dedupe"
                       " ON probables_history(game_id, away_starter_id,"
                       " home_starter_id)"),
    # One price, from one market, on one side of the book, at one moment. A
    # second row with that key is the same observation twice.
    ("ux_prices_dedupe", "CREATE UNIQUE INDEX IF NOT EXISTS ux_prices_dedupe"
                         " ON prices(market_id, outcome, quote, level,"
                         " captured_at)"),
    # fair_value and the fill simulator both ask "the prices for this market
    # at or after this moment".
    ("ix_prices_market_time", "CREATE INDEX IF NOT EXISTS ix_prices_market_time"
                              " ON prices(market_id, captured_at)"),
    ("ix_markets_event", "CREATE INDEX IF NOT EXISTS ix_markets_event"
                         " ON markets(canonical_event_id, market_type)"),
    ("ix_orders_strategy", "CREATE INDEX IF NOT EXISTS ix_orders_strategy"
                           " ON paper_orders(strategy, placed_at)"),
    ("ix_fills_order", "CREATE INDEX IF NOT EXISTS ix_fills_order"
                       " ON paper_fills(order_id)"),
    ("ix_ledger_ts", "CREATE INDEX IF NOT EXISTS ix_ledger_ts"
                     " ON credit_ledger(ts)"),
]
# closing_snapshot() filters odds_snapshots on game_id alone; that is the
# leading column of ux_snap_dedupe, so SQLite uses it. No separate index.


def index(con):
    """Create any missing index. Idempotent.

    A UNIQUE index fails to build if the table already violates it, which is
    information worth surfacing rather than swallowing: it means duplicate
    snapshots are already in there and the dedupe was not doing its job.
    """
    have = {r["name"] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    added = []
    for name, ddl in INDEXES:
        if name in have:
            continue
        try:
            con.execute(ddl)
            added.append(name)
        except sqlite3.IntegrityError as e:
            raise SystemExit(
                f"Cannot create {name}: {e}\n"
                "The table already contains rows that violate it. Inspect with:\n"
                "  SELECT game_id, ts, book, snapshot_type, COUNT(*) c\n"
                "  FROM odds_snapshots GROUP BY 1,2,3,4 HAVING c > 1;")
    return added


def migrate(con):
    """Add any columns a pre-existing DB is missing. Idempotent."""
    applied = []
    for table, column, coltype in MIGRATIONS:
        cols = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
        if not cols:
            continue          # table doesn't exist yet; SCHEMA just created it
        if column not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
            applied.append(f"{table}.{column}")
    return applied


def init():
    con = connect()
    con.executescript(SCHEMA)
    rebuilt = _rebuild_append_only(con)
    con.executescript(SCHEMA)
    applied = migrate(con)
    indexed = index(con)
    fixed = normalize_timestamps(con)
    redated = normalize_game_dates(con)
    con.commit()
    con.close()
    if rebuilt:
        print(f"Rebuilt append-only: {', '.join(rebuilt)}")
    if applied:
        print(f"Migrated: added {', '.join(applied)}")
    if indexed:
        print(f"Indexed: created {', '.join(indexed)}")
    if fixed:
        print(f"Normalized timestamps: {', '.join(fixed)}")
    if redated:
        print(f"Re-dated {redated} game(s) to their local first-pitch date")
    print(f"DB initialized at {DB_PATH}")


if __name__ == "__main__":
    init()
