-- SFP watch-state emit — additive migration (2026-06-26). RUNBOOK STEP 1.
-- Brand-new table (no existing data → no rebuild, unlike the bar_history migration).
-- Idempotent + additive: safe to run with the engine UP. The observer also runs
-- this same CREATE IF NOT EXISTS at init (defensive self-heal) — this explicit
-- step just makes the table exist before the new code loads + leaves an auditable
-- artifact. NOTHING is dropped or altered.
BEGIN;
CREATE TABLE IF NOT EXISTS sfp_watch_state (
    watch_id        TEXT PRIMARY KEY,    -- f"{symbol}:{mode}:{fired_bar_ts_ms}"
    fired_bar_ts    INTEGER NOT NULL,    -- ts_ms of the arming bar
    symbol          TEXT NOT NULL,       -- wire symbol, e.g. BTCUSDT
    mode            TEXT NOT NULL,       -- REAL | CONSIDERABLE
    swept_level     REAL NOT NULL,       -- swept pivot-low (invalidation line)
    swept_wick      REAL NOT NULL,       -- wick low that swept it
    bos_watch_level REAL,                -- arm-time BOS target; bos_ref_high on CONFIRMED
    status          TEXT NOT NULL,       -- ARMED | CONFIRMED | INVALIDATED | TIMED_OUT
    status_ts       TEXT NOT NULL,       -- ISO-8601 UTC of the latest transition
    armed_ts        TEXT NOT NULL,       -- ISO-8601 UTC when armed (preserved across updates)
    terminal_bar_ts INTEGER,             -- resolving bar ts_ms (NULL while ARMED)
    extra_json      TEXT                 -- {bos_ref_high, entry_bar_index} on CONFIRMED
);
CREATE INDEX IF NOT EXISTS ix_sfp_watch_state_status ON sfp_watch_state(status, status_ts);
CREATE INDEX IF NOT EXISTS ix_sfp_watch_state_symbol ON sfp_watch_state(symbol, status);
COMMIT;
-- verify
SELECT 'sfp_watch_state cols=' || COUNT(*) FROM pragma_table_info('sfp_watch_state');
