-- schema.sql
-- Single source of truth for trading.db
-- Applied automatically by db.py on first import

CREATE TABLE IF NOT EXISTS agent_state (
    agent_id          TEXT PRIMARY KEY,
    balance           REAL,
    peak_balance      REAL,
    starting_balance  REAL DEFAULT 150.0,
    position          TEXT,
    trading_paused    INTEGER DEFAULT 0,
    total_trades      INTEGER DEFAULT 0,
    cycle_count       INTEGER DEFAULT 0,
    last_indicators   TEXT,
    market_context    TEXT,
    triggers          TEXT,
    last_checked      TEXT,
    last_brain_call   TEXT,
    updated_at        TEXT
);

CREATE TABLE IF NOT EXISTS ohlcv (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id      TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    ts            TEXT NOT NULL,
    open          REAL,
    high          REAL,
    low           REAL,
    close         REAL,
    volume        REAL,
    interval_mins INTEGER,
    tier          INTEGER DEFAULT 1,
    UNIQUE(agent_id, symbol, ts, tier)
);

CREATE TABLE IF NOT EXISTS equity (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id  TEXT NOT NULL,
    ts        TEXT NOT NULL,
    balance   REAL NOT NULL,
    UNIQUE(agent_id, ts)
);

CREATE TABLE IF NOT EXISTS trades (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id          TEXT NOT NULL,
    symbol            TEXT,
    ts                TEXT NOT NULL,
    action            TEXT,
    side              TEXT,
    outcome           TEXT,
    confidence        REAL,
    position_size_pct REAL,
    entry_price       REAL,
    exit_price        REAL,
    pnl_pct           REAL,
    pnl_usd           REAL,
    fees_usd          REAL,
    balance_at_trade  REAL,
    reasoning         TEXT,
    triggers          TEXT,
    indicators        TEXT,
    strategy_update   TEXT,
    opened_at         TEXT,
    closed_at         TEXT
);

CREATE TABLE IF NOT EXISTS performance (
    agent_id            TEXT PRIMARY KEY,
    total_closed_trades INTEGER DEFAULT 0,
    wins                INTEGER DEFAULT 0,
    losses              INTEGER DEFAULT 0,
    win_rate            REAL DEFAULT 0,
    avg_win_pct         REAL DEFAULT 0,
    avg_loss_pct        REAL DEFAULT 0,
    profit_factor       REAL DEFAULT 0,
    total_pnl_usd       REAL DEFAULT 0,
    sharpe_approx       REAL DEFAULT 0,
    per_symbol          TEXT,
    updated_at          TEXT
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_agent_symbol_ts ON ohlcv(agent_id, symbol, ts);
CREATE INDEX IF NOT EXISTS idx_ohlcv_tier            ON ohlcv(agent_id, symbol, tier);
CREATE INDEX IF NOT EXISTS idx_equity_agent_ts       ON equity(agent_id, ts);
CREATE INDEX IF NOT EXISTS idx_trades_agent_ts       ON trades(agent_id, ts);
CREATE INDEX IF NOT EXISTS idx_trades_outcome        ON trades(agent_id, outcome);
