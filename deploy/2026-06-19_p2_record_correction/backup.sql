-- P2 record correction — BACKUP (run BEFORE apply.sql; operator-gated).
-- Snapshots the full paper_trade_record table into a timestamped backup table
-- inside the same DB, so apply.sql is fully reversible (see rollback.sql).
-- Run with: sqlite3 /home/azureuser/trading_corp/data/trading_corp.db < backup.sql
CREATE TABLE IF NOT EXISTS paper_trade_record_bak_pre_p2_2026_06_19 AS
  SELECT * FROM paper_trade_record;

-- Verify: the backup row-count must equal the live table (sanity before apply).
.headers on
.mode column
SELECT (SELECT COUNT(*) FROM paper_trade_record)                     AS live_rows,
       (SELECT COUNT(*) FROM paper_trade_record_bak_pre_p2_2026_06_19) AS backup_rows;
-- live_rows must == backup_rows. If not → STOP (do not run apply.sql).
