-- Canonical DDL for the TC2000 + Alpaca swing system.
-- Portable subset: runs on SQLite (dev/tests) and PostgreSQL (deployment).
-- Timestamps are ISO-8601 UTC TEXT; JSON payloads are TEXT (jsonb in Postgres via migration).
-- Mirrors docs/db_schema.md. Every decision is reconstructable from stored inputs + config.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tc2000_imports (
    id            TEXT PRIMARY KEY,
    market_date   TEXT NOT NULL,          -- YYYY-MM-DD (America/New_York)
    received_at   TEXT NOT NULL,
    batch_hash    TEXT NOT NULL UNIQUE,
    status        TEXT NOT NULL,          -- ACCEPTED | REJECTED
    reject_reason TEXT,
    config_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tc2000_scan_files (
    id           TEXT PRIMARY KEY,
    import_id    TEXT NOT NULL REFERENCES tc2000_imports(id),
    scan         TEXT NOT NULL,           -- one_month | three_month | six_month
    filename     TEXT NOT NULL,
    file_hash    TEXT NOT NULL,
    raw_path     TEXT,
    symbol_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_membership (
    id              TEXT PRIMARY KEY,
    import_id       TEXT NOT NULL REFERENCES tc2000_imports(id),
    symbol          TEXT NOT NULL,
    in_1m           INTEGER NOT NULL,
    in_3m           INTEGER NOT NULL,
    in_6m           INTEGER NOT NULL,
    agreement_count INTEGER NOT NULL,
    mode_3of3       INTEGER NOT NULL,
    mode_2of3       INTEGER NOT NULL,
    mode_union      INTEGER NOT NULL,
    composite_strength REAL,
    source          TEXT NOT NULL DEFAULT 'TC2000'
);
CREATE INDEX IF NOT EXISTS ix_candidate_import ON candidate_membership(import_id);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id                TEXT PRIMARY KEY,
    symbol            TEXT NOT NULL,
    as_of             TEXT NOT NULL,
    feed              TEXT NOT NULL,       -- iex | sip
    bar_timestamp     TEXT NOT NULL,
    adjusted          INTEGER NOT NULL,
    ohlcv_json        TEXT NOT NULL,
    staleness_seconds REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_snapshot_symbol ON market_snapshots(symbol, as_of);

CREATE TABLE IF NOT EXISTS indicator_values (
    id          TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES market_snapshots(id),
    symbol      TEXT NOT NULL,
    as_of       TEXT NOT NULL,
    timeframe   TEXT NOT NULL,             -- daily | hourly
    sma10 REAL, sma20 REAL, sma50 REAL, sma200 REAL,
    vol_ema22 REAL, adr_pct REAL, atr_pct REAL, dollar_volume REAL,
    slope10 REAL, slope20 REAL, slope50 REAL, slope200 REAL
);

CREATE TABLE IF NOT EXISTS setups (
    id                TEXT PRIMARY KEY,
    symbol            TEXT NOT NULL,
    setup_version     TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    contraction_start TEXT,
    contraction_end   TEXT,
    breakout_level    REAL,
    components_json   TEXT NOT NULL,
    setup_score       REAL NOT NULL,
    required_pass     INTEGER NOT NULL,
    config_version    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_calcs (
    id             TEXT PRIMARY KEY,
    signal_id      TEXT,
    risk_equity    REAL NOT NULL,
    risk_fraction  REAL NOT NULL,
    expected_entry REAL NOT NULL,
    initial_stop   REAL NOT NULL,
    risk_per_share REAL NOT NULL,
    risk_dollars   REAL NOT NULL,
    raw_shares     INTEGER NOT NULL,
    capped_shares  INTEGER NOT NULL,
    caps_json      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id              TEXT PRIMARY KEY,
    symbol          TEXT NOT NULL,
    setup_id        TEXT REFERENCES setups(id),
    created_at      TEXT NOT NULL,
    kind            TEXT NOT NULL,          -- ENTRY | PARTIAL | FINAL_EXIT
    state           TEXT NOT NULL,
    accepted        INTEGER NOT NULL,
    reject_reason   TEXT,
    idempotency_key TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS orders (
    id              TEXT PRIMARY KEY,
    signal_id       TEXT REFERENCES signals(id),
    client_order_id TEXT NOT NULL UNIQUE,   -- idempotent
    broker_order_id TEXT,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    type            TEXT NOT NULL,          -- limit | stop | stop_limit
    limit_price     REAL,
    stop_price      REAL,
    qty             INTEGER NOT NULL,
    status          TEXT NOT NULL,
    submitted_at    TEXT,
    acknowledged_at TEXT
);

CREATE TABLE IF NOT EXISTS fills (
    id             TEXT PRIMARY KEY,
    order_id       TEXT NOT NULL REFERENCES orders(id),
    fill_qty       INTEGER NOT NULL,
    fill_price     REAL NOT NULL,
    filled_at      TEXT NOT NULL,
    cumulative_qty INTEGER NOT NULL,
    vwap_price     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS order_replacements (
    id       TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(id),
    reason   TEXT NOT NULL,
    old_stop REAL, new_stop REAL,
    at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS position_state_transitions (
    id                 TEXT PRIMARY KEY,
    trade_id           TEXT NOT NULL,
    from_state         TEXT NOT NULL,
    to_state           TEXT NOT NULL,
    guard_results_json TEXT NOT NULL,
    idempotency_key    TEXT NOT NULL UNIQUE,
    reason             TEXT NOT NULL,
    at                 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_transition_trade ON position_state_transitions(trade_id, at);

CREATE TABLE IF NOT EXISTS daily_account_snapshots (
    id             TEXT PRIMARY KEY,
    session_date   TEXT NOT NULL,
    equity         REAL NOT NULL,
    cash           REAL NOT NULL,
    buying_power   REAL NOT NULL,
    committed_risk REAL NOT NULL,
    exposure       REAL NOT NULL,
    realized_pnl   REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    drawdown       REAL NOT NULL,
    endpoint       TEXT NOT NULL           -- must equal the verified paper endpoint
);

CREATE TABLE IF NOT EXISTS pnl (
    id          TEXT PRIMARY KEY,
    trade_id    TEXT NOT NULL,
    realized    REAL NOT NULL,
    unrealized  REAL NOT NULL,
    r_multiple  REAL,
    as_of       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_reviews (
    id             TEXT PRIMARY KEY,
    subject_type   TEXT NOT NULL,
    subject_id     TEXT NOT NULL,
    model_name     TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    prompt         TEXT NOT NULL,
    output         TEXT NOT NULL,
    tokens_in      INTEGER, tokens_out INTEGER, cost_usd REAL,
    at             TEXT NOT NULL           -- advisory only, never authoritative
);

CREATE TABLE IF NOT EXISTS reconciliation_incidents (
    id               TEXT PRIMARY KEY,
    detected_at      TEXT NOT NULL,
    kind             TEXT NOT NULL,
    symbol           TEXT,
    broker_state_json TEXT,
    db_state_json    TEXT,
    detail           TEXT,
    resolved         INTEGER NOT NULL DEFAULT 0,
    resolution_note  TEXT
);
