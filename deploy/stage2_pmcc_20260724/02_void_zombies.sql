-- =====================================================================
-- Stage-2 ZOMBIE PRUNE (PREPARED — do NOT run outside operator-authorized Stage 2)
-- Void 4 stale board_approved zombie rows -> board_rejected.
--
-- These are pre-existing (>=16 days old, none from 2026-07-24), carry no combo_id,
-- and PMCC rebuilds its book from the broker each scan so they cannot re-dispatch.
-- Prune them so they can't be mistaken for actionable (the c870a9e6 lesson):
--   3dcb946a ASTS 2026-05-08 | f493dac0 ASTS 2026-05-08 |
--   fa7a3749 ASTS 2026-05-21 | dd2822af CIFR 2026-07-08
--
-- IDEMPOTENT: guarded by id + status='board_approved' (re-run is a no-op).
-- RUN:  sqlite3 /home/azureuser/trading_corp/data/trading_corp.db < 02_void_zombies.sql
-- =====================================================================
.mode column
.headers on

.print === ZOMBIES BEFORE (expect 4 x board_approved) ===
SELECT id, ts, symbol, status FROM proposed_order
WHERE id IN ('3dcb946a-038e-425a-bfb0-6bc4b571f90f',
             'f493dac0-f4bd-458e-9e06-137170231207',
             'fa7a3749-bc95-45b8-a6ad-a76791891a77',
             'dd2822af-67e7-4aa1-81a8-af408d8f6672')
ORDER BY ts;

BEGIN;
UPDATE proposed_order
   SET status='board_rejected',
       board_reason='voided 2026-07-24: stale board_approved zombie (>=16d old, no combo_id; PMCC rebuilds book each scan so cannot re-dispatch) - see reports/2026-07-24_pmcc_first_live_morning_healthcheck.md'
 WHERE id IN ('3dcb946a-038e-425a-bfb0-6bc4b571f90f',
              'f493dac0-f4bd-458e-9e06-137170231207',
              'fa7a3749-bc95-45b8-a6ad-a76791891a77',
              'dd2822af-67e7-4aa1-81a8-af408d8f6672')
   AND status='board_approved';
COMMIT;

.print === AFTER (expect 4 x board_rejected) ===
SELECT id, symbol, status, board_reason FROM proposed_order
WHERE id IN ('3dcb946a-038e-425a-bfb0-6bc4b571f90f',
             'f493dac0-f4bd-458e-9e06-137170231207',
             'fa7a3749-bc95-45b8-a6ad-a76791891a77',
             'dd2822af-67e7-4aa1-81a8-af408d8f6672')
ORDER BY ts;

.print === RESIDUAL ACTIONABLE PMCC ROWS (expect 0) ===
SELECT COUNT(*) AS still_actionable FROM proposed_order
WHERE strategy='robinhood_pmcc' AND status IN ('board_approved','proposed','risk_approved');
