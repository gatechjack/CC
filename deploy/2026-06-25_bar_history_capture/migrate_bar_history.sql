-- Migrate bitunix_bar_history to PK (symbol, ts_ms, timeframe) — 2026-06-25.
-- The old PK (ts_ms, timeframe) had no symbol, so all coins' same-TF bars
-- collided (INSERT OR IGNORE kept BTC). Run OFFLINE (engine stopped) on the
-- TRADING db:
--     sqlite3 ~/trading_corp/data/trading_corp.db < migrate_bar_history.sql
-- Transactional: any failure ROLLBACKs to the pre-state. The backup table
-- bitunix_bar_history_bak_20260625 is belt-and-suspenders recovery (drop it
-- after a few clean days). Operator has NOPASSWD sqlite3.
.bail on
.echo on
SELECT 'pre_rows' AS k, COUNT(*) AS v FROM bitunix_bar_history;   -- note N
BEGIN;
CREATE TABLE bitunix_bar_history_bak_20260625 AS SELECT * FROM bitunix_bar_history;
CREATE TABLE bitunix_bar_history_new (
    symbol       TEXT NOT NULL,
    ts_ms        INTEGER NOT NULL,
    timeframe    TEXT NOT NULL,
    open         REAL NOT NULL,
    high         REAL NOT NULL,
    low          REAL NOT NULL,
    close        REAL NOT NULL,
    volume       REAL NOT NULL,
    inserted_at  TEXT NOT NULL,
    PRIMARY KEY (symbol, ts_ms, timeframe)
);
INSERT INTO bitunix_bar_history_new
    (symbol, ts_ms, timeframe, open, high, low, close, volume, inserted_at)
    SELECT 'BTCUSDT', ts_ms, timeframe, open, high, low, close, volume, inserted_at
    FROM bitunix_bar_history;
DROP TABLE bitunix_bar_history;
ALTER TABLE bitunix_bar_history_new RENAME TO bitunix_bar_history;
CREATE INDEX bitunix_bar_history_sym_tf_ts_idx ON bitunix_bar_history(symbol, timeframe, ts_ms);
COMMIT;
-- VERIFY: post_rows must equal pre_rows (N); n_symbols must be 1 (all BTCUSDT);
-- backup_rows must equal N. If post_rows != pre_rows, STOP and restore the backup.
SELECT 'post_rows' AS k, COUNT(*) AS v FROM bitunix_bar_history
UNION ALL SELECT 'n_symbols', COUNT(DISTINCT symbol) FROM bitunix_bar_history
UNION ALL SELECT 'backup_rows', COUNT(*) FROM bitunix_bar_history_bak_20260625;
