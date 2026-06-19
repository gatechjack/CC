-- P2 classifier historical-record correction — READ-ONLY DRY-RUN.
-- Run with: sqlite3 -readonly /home/azureuser/trading_corp/data/trading_corp.db < dryrun.sql
-- Shows EXACTLY what apply.sql would change. Touches nothing. LABEL-ONLY scope:
-- result + autobook_level_type + exit_kind. PnL values (actual_pnl_dollars,
-- actual_r_multiple, net_realized_usd) are NEVER touched.
.headers on
.mode column

SELECT '--- A. the 2 target records: CURRENT -> INTENDED (label-only) ---' AS note;
SELECT order_id,
       result            AS old_result,
       'win'             AS new_result,
       json_extract(extra_json,'$.autobook_level_type') AS old_lvl,
       'tp'              AS new_lvl,
       json_extract(extra_json,'$.exit_kind')           AS old_exit_kind,
       'tp'              AS new_exit_kind,
       round(actual_pnl_dollars,5)                       AS gross_pnl_UNCHANGED,
       round(json_extract(extra_json,'$.net_realized_usd'),5) AS net_UNCHANGED,
       round(actual_r_multiple,4)                        AS r_UNCHANGED
FROM paper_trade_record
WHERE order_id IN ('e1758fc9-e350-404a-ba7d-41fed78b09dc',
                   '7d1a78dc-2654-46a8-86e2-f68945e5c083');

SELECT '--- B. PROOF no OTHER win/loss record disagrees with its PnL sign ---' AS note;
-- result IN ('win','loss') only — 'expired'/'breakeven' are valid non-PnL states
-- (a 0-PnL no-fill max-hold expiry is 'expired', NOT a loss) and are NOT in scope.
SELECT order_id, execution_mode, result,
       round(actual_pnl_dollars,5) AS gross,
       round(json_extract(extra_json,'$.net_realized_usd'),5) AS net
FROM paper_trade_record
WHERE division LIKE '%bitunix%' AND result IN ('win','loss')
  AND result != (CASE WHEN COALESCE(json_extract(extra_json,'$.net_realized_usd'),
                                    actual_pnl_dollars) > 0 THEN 'win' ELSE 'loss' END);
-- Expected: exactly the 2 target rows above. Any other row → STOP, re-review.
