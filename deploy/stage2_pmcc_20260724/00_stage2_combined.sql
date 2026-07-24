-- =====================================================================
-- Stage-2 COMBINED DATA FIX — assertion-guarded single transaction.
-- Merges 01_fix_signflip_fills.sql + 02_void_zombies.sql into ONE all-or-
-- nothing transaction with HARD pre/post assertions. Any mismatch aborts
-- and the open transaction rolls back (nothing committed).
--
-- Mechanism: `.bail on` + a TEMP TABLE with CHECK(x=1); INSERTing 0 (guard
-- false) violates the CHECK -> runtime error -> .bail exits nonzero -> the
-- BEGIN transaction (never COMMITted) is rolled back on connection close.
--
-- Float-safe: all numeric compares use ABS(a-b) < 0.005.
-- RUN (engine STOPPED): sqlite3 <db> < 00_stage2_combined.sql ; echo "exit=$?"
-- =====================================================================
.bail on
.mode column
.headers on
.print ===== STAGE-2 COMBINED (signflip fills + zombie void), assertion-guarded =====

.print --- BEFORE: signflip rows (expect swapped 084664a8=0.29, cd4b8be3=0.03, fef29c33=1.2, 4eac1925=0.03) ---
SELECT id, symbol, side, fill_price, status FROM proposed_order
 WHERE id IN ('084664a8-8353-4073-8294-d56b80bc0fee','cd4b8be3-1390-4ed5-8ca2-cc2bd2eb6ba4',
              'fef29c33-d521-4018-aadb-e8db7f1150eb','4eac1925-4272-469a-8204-48cc1f9a58fc')
 ORDER BY ts;
.print --- BEFORE: zombies (expect 4x board_approved) ---
SELECT id, ts, symbol, status FROM proposed_order
 WHERE id IN ('3dcb946a-038e-425a-bfb0-6bc4b571f90f','f493dac0-f4bd-458e-9e06-137170231207',
              'fa7a3749-bc95-45b8-a6ad-a76791891a77','dd2822af-67e7-4aa1-81a8-af408d8f6672')
 ORDER BY ts;

BEGIN;

.print [ASSERT PRE-1] exactly 4 signflip rows in the swapped state...
CREATE TEMP TABLE _a1(x INTEGER CHECK(x=1));
INSERT INTO _a1 VALUES ((SELECT CASE WHEN (
  SELECT count(*) FROM proposed_order WHERE
    (id='084664a8-8353-4073-8294-d56b80bc0fee' AND symbol='OPEN' AND side='buy'  AND status='filled' AND ABS(fill_price-0.29)<0.005) OR
    (id='cd4b8be3-1390-4ed5-8ca2-cc2bd2eb6ba4' AND symbol='OPEN' AND side='sell' AND status='filled' AND ABS(fill_price-0.03)<0.005) OR
    (id='fef29c33-d521-4018-aadb-e8db7f1150eb' AND symbol='RKLB' AND side='buy'  AND status='filled' AND ABS(fill_price-1.20)<0.005) OR
    (id='4eac1925-4272-469a-8204-48cc1f9a58fc' AND symbol='RKLB' AND side='sell' AND status='filled' AND ABS(fill_price-0.03)<0.005)
  )=4 THEN 1 ELSE 0 END));

.print [ASSERT PRE-2] exactly 4 zombies board_approved...
CREATE TEMP TABLE _a2(x INTEGER CHECK(x=1));
INSERT INTO _a2 VALUES ((SELECT CASE WHEN (
  SELECT count(*) FROM proposed_order WHERE status='board_approved' AND id IN (
    '3dcb946a-038e-425a-bfb0-6bc4b571f90f','f493dac0-f4bd-458e-9e06-137170231207',
    'fa7a3749-bc95-45b8-a6ad-a76791891a77','dd2822af-67e7-4aa1-81a8-af408d8f6672')
  )=4 THEN 1 ELSE 0 END));

.print [APPLY] signflip fix (4 rows) + zombie void (4 rows)...
UPDATE proposed_order SET fill_price=0.03 WHERE id='084664a8-8353-4073-8294-d56b80bc0fee' AND symbol='OPEN' AND side='buy'  AND status='filled';
UPDATE proposed_order SET fill_price=0.29 WHERE id='cd4b8be3-1390-4ed5-8ca2-cc2bd2eb6ba4' AND symbol='OPEN' AND side='sell' AND status='filled';
UPDATE proposed_order SET fill_price=0.03 WHERE id='fef29c33-d521-4018-aadb-e8db7f1150eb' AND symbol='RKLB' AND side='buy'  AND status='filled';
UPDATE proposed_order SET fill_price=1.20 WHERE id='4eac1925-4272-469a-8204-48cc1f9a58fc' AND symbol='RKLB' AND side='sell' AND status='filled';
UPDATE proposed_order
   SET status='board_rejected',
       board_reason='voided 2026-07-24: stale board_approved zombie (>=16d old, no combo_id; PMCC rebuilds book each scan so cannot re-dispatch) - see reports/2026-07-24_pmcc_first_live_morning_healthcheck.md'
 WHERE id IN ('3dcb946a-038e-425a-bfb0-6bc4b571f90f','f493dac0-f4bd-458e-9e06-137170231207',
              'fa7a3749-bc95-45b8-a6ad-a76791891a77','dd2822af-67e7-4aa1-81a8-af408d8f6672')
   AND status='board_approved';

.print [ASSERT POST-1] OPEN net credit = +0.26...
CREATE TEMP TABLE _a3(x INTEGER CHECK(x=1));
INSERT INTO _a3 VALUES ((SELECT CASE WHEN ABS((
  SELECT ROUND(SUM(CASE WHEN side='sell' THEN fill_price ELSE -fill_price END),2)
  FROM proposed_order WHERE id IN ('084664a8-8353-4073-8294-d56b80bc0fee','cd4b8be3-1390-4ed5-8ca2-cc2bd2eb6ba4'))-0.26)<0.005 THEN 1 ELSE 0 END));

.print [ASSERT POST-2] RKLB net credit = +1.17...
CREATE TEMP TABLE _a4(x INTEGER CHECK(x=1));
INSERT INTO _a4 VALUES ((SELECT CASE WHEN ABS((
  SELECT ROUND(SUM(CASE WHEN side='sell' THEN fill_price ELSE -fill_price END),2)
  FROM proposed_order WHERE id IN ('fef29c33-d521-4018-aadb-e8db7f1150eb','4eac1925-4272-469a-8204-48cc1f9a58fc'))-1.17)<0.005 THEN 1 ELSE 0 END));

.print [ASSERT POST-3] 4 zombies now rejected, 0 board_approved remain...
CREATE TEMP TABLE _a5(x INTEGER CHECK(x=1));
INSERT INTO _a5 VALUES ((SELECT CASE WHEN (
  SELECT count(*) FROM proposed_order WHERE status='board_approved' AND id IN (
    '3dcb946a-038e-425a-bfb0-6bc4b571f90f','f493dac0-f4bd-458e-9e06-137170231207',
    'fa7a3749-bc95-45b8-a6ad-a76791891a77','dd2822af-67e7-4aa1-81a8-af408d8f6672')
  )=0 THEN 1 ELSE 0 END));

.print [ASSERT POST-4] 0 residual actionable PMCC rows...
CREATE TEMP TABLE _a6(x INTEGER CHECK(x=1));
INSERT INTO _a6 VALUES ((SELECT CASE WHEN (
  SELECT count(*) FROM proposed_order WHERE strategy='robinhood_pmcc'
    AND status IN ('board_approved','proposed','risk_approved')
  )=0 THEN 1 ELSE 0 END));

DROP TABLE _a1; DROP TABLE _a2; DROP TABLE _a3; DROP TABLE _a4; DROP TABLE _a5; DROP TABLE _a6;
COMMIT;
.print [COMMIT OK] all assertions passed.

.print ===== AFTER (committed) =====
.print --- signflip rows (expect buy=0.03; OPEN sell=0.29; RKLB sell=1.20) ---
SELECT id, symbol, side, fill_price FROM proposed_order
 WHERE id IN ('084664a8-8353-4073-8294-d56b80bc0fee','cd4b8be3-1390-4ed5-8ca2-cc2bd2eb6ba4',
              'fef29c33-d521-4018-aadb-e8db7f1150eb','4eac1925-4272-469a-8204-48cc1f9a58fc')
 ORDER BY ts;
.print --- net (expect OPEN 0.26, RKLB 1.17) ---
SELECT 'OPEN' AS pair, ROUND(SUM(CASE WHEN side='sell' THEN fill_price ELSE -fill_price END),2) AS net_credit
 FROM proposed_order WHERE id IN ('084664a8-8353-4073-8294-d56b80bc0fee','cd4b8be3-1390-4ed5-8ca2-cc2bd2eb6ba4');
SELECT 'RKLB' AS pair, ROUND(SUM(CASE WHEN side='sell' THEN fill_price ELSE -fill_price END),2) AS net_credit
 FROM proposed_order WHERE id IN ('fef29c33-d521-4018-aadb-e8db7f1150eb','4eac1925-4272-469a-8204-48cc1f9a58fc');
.print --- zombies (expect 4x board_rejected) ---
SELECT id, symbol, status FROM proposed_order
 WHERE id IN ('3dcb946a-038e-425a-bfb0-6bc4b571f90f','f493dac0-f4bd-458e-9e06-137170231207',
              'fa7a3749-bc95-45b8-a6ad-a76791891a77','dd2822af-67e7-4aa1-81a8-af408d8f6672')
 ORDER BY ts;
.print --- residual actionable PMCC (expect 0) ---
SELECT COUNT(*) AS still_actionable FROM proposed_order
 WHERE strategy='robinhood_pmcc' AND status IN ('board_approved','proposed','risk_approved');
