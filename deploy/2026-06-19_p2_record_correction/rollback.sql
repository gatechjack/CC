-- P2 record correction — ROLLBACK. Restores the 2 corrected rows from the
-- pre-correction backup table (portable correlated-subquery form; no UPDATE..FROM).
-- Run with: sqlite3 /home/azureuser/trading_corp/data/trading_corp.db < rollback.sql
UPDATE paper_trade_record
SET result = (SELECT b.result FROM paper_trade_record_bak_pre_p2_2026_06_19 b
              WHERE b.order_id = paper_trade_record.order_id),
    extra_json = (SELECT b.extra_json FROM paper_trade_record_bak_pre_p2_2026_06_19 b
                  WHERE b.order_id = paper_trade_record.order_id)
WHERE order_id IN ('e1758fc9-e350-404a-ba7d-41fed78b09dc',
                   '7d1a78dc-2654-46a8-86e2-f68945e5c083');
.headers on
.mode column
SELECT order_id, result, json_extract(extra_json,'$.exit_kind') AS exit_kind
FROM paper_trade_record
WHERE order_id IN ('e1758fc9-e350-404a-ba7d-41fed78b09dc',
                   '7d1a78dc-2654-46a8-86e2-f68945e5c083');
-- Expect both back to result=loss / exit_kind=null (the pre-correction state).
