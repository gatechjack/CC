-- P2 record correction — APPLY (LABEL-ONLY). DO NOT RUN until the operator has
-- reviewed dryrun.sql and run backup.sql. Board-gated prod-data write.
-- Run with: sqlite3 /home/azureuser/trading_corp/data/trading_corp.db < apply.sql
--
-- Corrects ONLY the categorical labels the P2 auto-book hard-coded wrong on 2
-- genuine wins. NEVER touches actual_pnl_dollars / actual_r_multiple /
-- net_realized_usd (those are correct). Scoped to the 2 diagnosed order_ids and
-- guarded by (result='loss' AND actual_pnl_dollars > 0) so it is idempotent and
-- cannot touch a real loss or an 'expired' row.

-- e1758fc9 — maker TP fill (fill 64478.8 == tp1 64478.83), booked loss/stop.
UPDATE paper_trade_record
SET result = 'win',
    extra_json = json_set(extra_json,
        '$.autobook_level_type', 'tp',
        '$.exit_kind', 'tp',
        '$.label_corrected', json('true'),
        '$.label_corrected_ts', '2026-06-19',
        '$.label_corrected_from', json('{"result":"loss","exit_kind":"stop"}'))
WHERE order_id = 'e1758fc9-e350-404a-ba7d-41fed78b09dc'
  AND result = 'loss' AND actual_pnl_dollars > 0;

-- 7d1a78dc — favorable multi-fill TP-driven close (past tp1/tp2), booked loss/stop.
UPDATE paper_trade_record
SET result = 'win',
    extra_json = json_set(extra_json,
        '$.autobook_level_type', 'tp',
        '$.exit_kind', 'tp',
        '$.label_corrected', json('true'),
        '$.label_corrected_ts', '2026-06-19',
        '$.label_corrected_from', json('{"result":"loss","exit_kind":"stop"}'))
WHERE order_id = '7d1a78dc-2654-46a8-86e2-f68945e5c083'
  AND result = 'loss' AND actual_pnl_dollars > 0;

-- POST-APPLY VERIFY (must show both rows result=win / exit_kind=tp, PnL unchanged).
.headers on
.mode column
SELECT order_id, result,
       json_extract(extra_json,'$.exit_kind') AS exit_kind,
       json_extract(extra_json,'$.autobook_level_type') AS lvl,
       round(actual_pnl_dollars,5) AS gross, round(actual_r_multiple,4) AS r
FROM paper_trade_record
WHERE order_id IN ('e1758fc9-e350-404a-ba7d-41fed78b09dc',
                   '7d1a78dc-2654-46a8-86e2-f68945e5c083');
