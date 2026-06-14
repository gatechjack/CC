-- P2 booking — STEP 1: confirming SELECT. Run FIRST on a (writable or readonly)
-- connection. Must return EXACTLY 1 row before running the Step-2 UPDATE.
--   0 rows  => already booked (result not NULL) => do NOT run the UPDATE.
--   >1 rows => STOP (WHERE too broad) => do NOT run the UPDATE.
-- DRAFT for operator review — agent does NOT execute. See
-- runbooks/2026-06-14_bitunix_p1_deploy_batch_plan.md §3.
SELECT order_id, ts, side, qty, entry_reference_price, stop_price,
       result, result_price, actual_pnl_dollars,
       json_extract(extra_json,'$.execution_mode') AS mode,
       json_extract(extra_json,'$.entry_fee_usd')  AS entry_fee,
       json_extract(extra_json,'$.exit_fee_usd')   AS exit_fee
FROM paper_trade_record
WHERE order_id = '6741f62f-d950-4356-8deb-578f603f8db0'
  AND result IS NULL;
