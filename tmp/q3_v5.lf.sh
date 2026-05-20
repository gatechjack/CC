#!/bin/bash
echo "===== A. PM round_trips with whale_closed result ====="
sqlite3 -header -column /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT 'pm_whale_closed_total' AS metric, COUNT(*) AS n FROM polymarket_round_trips
 WHERE json_extract(extra_json, '$.market_result')='whale_closed';
SELECT 'pm_with_entry_order_id' AS metric, COUNT(*) AS n FROM polymarket_round_trips
 WHERE entry_order_id IS NOT NULL;
SELECT 'ks_whale_closed_total' AS metric, COUNT(*) AS n FROM kalshi_round_trips
 WHERE json_extract(extra_json, '$.market_result')='whale_closed';
SELECT 'ks_with_entry_order_id' AS metric, COUNT(*) AS n FROM kalshi_round_trips
 WHERE entry_order_id IS NOT NULL;
SQL

echo ""
echo "===== B. Sample of synthetic SELL audit payload ====="
sqlite3 -separator ' | ' /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT ts, substr(payload_json, 1, 200)
  FROM audit_event
 WHERE kind='would_have_placed'
   AND json_extract(payload_json, '$.is_synthetic_close')=1
   AND json_extract(payload_json, '$.strategy')='polymarket_copy_trader'
 ORDER BY ts ASC LIMIT 2;
SQL

echo ""
echo "===== C. How many synthetic SELLs are unpaired? ====="
sqlite3 -header -column /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT 'pm_synth_sells_unpaired' AS metric, COUNT(*) AS n
  FROM audit_event a
  LEFT JOIN polymarket_round_trips r
    ON r.order_id = json_extract(a.payload_json, '$.order_id')
 WHERE a.kind='would_have_placed'
   AND json_extract(a.payload_json, '$.is_synthetic_close')=1
   AND json_extract(a.payload_json, '$.strategy')='polymarket_copy_trader'
   AND r.order_id IS NULL;
SELECT 'pm_synth_sells_paired' AS metric, COUNT(*) AS n
  FROM audit_event a
  JOIN polymarket_round_trips r
    ON r.order_id = json_extract(a.payload_json, '$.order_id')
 WHERE a.kind='would_have_placed'
   AND json_extract(a.payload_json, '$.is_synthetic_close')=1
   AND json_extract(a.payload_json, '$.strategy')='polymarket_copy_trader';
SQL

echo ""
echo "===== D. Is _pair_pending_exits even invoked? ====="
sqlite3 -separator ' | ' /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT kind, COUNT(*) AS n
  FROM audit_event
 WHERE kind LIKE '%resolver%' OR kind LIKE '%paired%' OR kind LIKE '%pair_exits%'
 GROUP BY kind
 ORDER BY n DESC LIMIT 10;
SQL

echo ""
echo "===== E. Look for matching BUY for one synthetic SELL ====="
sqlite3 -header -column /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
WITH ss AS (
  SELECT ts, payload_json,
         json_extract(payload_json, '$.whale_wallet') AS wallet,
         json_extract(payload_json, '$.condition_id') AS cid,
         json_extract(payload_json, '$.outcome_index') AS oidx
    FROM audit_event
   WHERE kind='would_have_placed'
     AND json_extract(payload_json, '$.is_synthetic_close')=1
     AND json_extract(payload_json, '$.strategy')='polymarket_copy_trader'
   LIMIT 1
)
SELECT
  (SELECT cid FROM ss)    AS sell_cid,
  (SELECT oidx FROM ss)   AS sell_oidx,
  (SELECT substr(wallet, 1, 12) FROM ss) AS sell_wallet_short,
  (SELECT ts FROM ss)     AS sell_ts,
  (
    SELECT COUNT(*) FROM audit_event a
    WHERE a.kind='would_have_placed'
      AND a.actor='polymarket_copy_trader'
      AND json_extract(a.payload_json, '$.side')='buy'
      AND json_extract(a.payload_json, '$.whale_wallet')=(SELECT wallet FROM ss)
      AND json_extract(a.payload_json, '$.condition_id')=(SELECT cid FROM ss)
      AND json_extract(a.payload_json, '$.outcome_index')=(SELECT oidx FROM ss)
      AND a.ts < (SELECT ts FROM ss)
  ) AS matching_buys;
SQL

echo ""
echo "===== F. journalctl tail — resolver activity ====="
journalctl -u trading-corp --since "2026-05-17 17:00" --no-pager 2>&1 | grep -Ei "resolver|pair_pending_exits|paired|whale_closed" | head -20 || echo "no matches"
