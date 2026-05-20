#!/bin/bash
DB=/home/azureuser/trading_corp/data/trading_corp.db
echo "===== A. Trade activity since 2026-04-01 ====="
sqlite3 -header -column $DB <<'SQL'
SELECT
  COUNT(*) AS n_round_trips,
  SUM(realized_pnl) AS total_pnl,
  ROUND(AVG(realized_pnl), 2) AS avg_pnl,
  SUM(won) AS wins,
  SUM(CASE WHEN won=0 AND market_result IN ('yes','no') THEN 1 ELSE 0 END) AS losses,
  SUM(CASE WHEN market_result='void' THEN 1 ELSE 0 END) AS voids,
  ROUND(SUM(notional), 0) AS gross_notional
FROM kalshi_round_trips
WHERE strategy='kalshi_crypto_arb'
  AND entry_ts > '2026-04-01';
SQL

echo ""
echo "===== B. P&L by day (last 30 days) ====="
sqlite3 -header -column $DB <<'SQL'
SELECT substr(entry_ts, 1, 10) AS day,
       COUNT(*) AS n,
       SUM(won) AS w,
       ROUND(SUM(realized_pnl), 2) AS pnl,
       ROUND(SUM(notional), 0) AS notional
FROM kalshi_round_trips
WHERE strategy='kalshi_crypto_arb'
  AND entry_ts > date('now', '-30 days')
GROUP BY day
ORDER BY day DESC
LIMIT 30;
SQL

echo ""
echo "===== C. PnL by event_ticker (top losers/winners) ====="
sqlite3 -header -column $DB <<'SQL'
SELECT event_ticker,
       COUNT(*) AS n,
       SUM(won) AS w,
       ROUND(SUM(realized_pnl), 2) AS pnl,
       ROUND(AVG(divergence_pct), 1) AS avg_div,
       ROUND(AVG(implied_at_entry), 3) AS avg_impl,
       ROUND(AVG(llm_prob), 3) AS avg_p_yes
FROM kalshi_round_trips
WHERE strategy='kalshi_crypto_arb'
  AND entry_ts > '2026-04-01'
GROUP BY event_ticker
HAVING n >= 3
ORDER BY pnl ASC
LIMIT 20;
SQL

echo ""
echo "===== D. Divergence calibration — actual win rate by div bucket ====="
sqlite3 -header -column $DB <<'SQL'
WITH bucketed AS (
  SELECT CASE
           WHEN divergence_pct < 15 THEN '10-15%'
           WHEN divergence_pct < 20 THEN '15-20%'
           WHEN divergence_pct < 30 THEN '20-30%'
           WHEN divergence_pct < 50 THEN '30-50%'
           ELSE '50%+'
         END AS div_bucket,
         won,
         realized_pnl,
         notional
  FROM kalshi_round_trips
  WHERE strategy='kalshi_crypto_arb'
    AND entry_ts > '2026-04-01'
    AND market_result IN ('yes','no')
)
SELECT div_bucket,
       COUNT(*) AS n,
       SUM(won) AS w,
       ROUND(100.0*SUM(won)/COUNT(*), 1) AS actual_wr_pct,
       ROUND(SUM(realized_pnl), 2) AS pnl,
       ROUND(100.0*SUM(realized_pnl)/SUM(notional), 1) AS roi_pct
FROM bucketed
GROUP BY div_bucket
ORDER BY div_bucket;
SQL

echo ""
echo "===== E. Outcome bet side — yes vs no skew ====="
sqlite3 -header -column $DB <<'SQL'
SELECT outcome_bet,
       COUNT(*) AS n,
       SUM(won) AS w,
       ROUND(100.0*SUM(won)/COUNT(*), 1) AS wr_pct,
       ROUND(SUM(realized_pnl), 2) AS pnl,
       ROUND(AVG(implied_at_entry), 3) AS avg_implied
FROM kalshi_round_trips
WHERE strategy='kalshi_crypto_arb'
  AND entry_ts > '2026-04-01'
  AND market_result IN ('yes','no')
GROUP BY outcome_bet;
SQL

echo ""
echo "===== F. Horizon bucket (hours-to-resolution at entry, est from extra_json) ====="
sqlite3 -header -column $DB <<'SQL'
SELECT CASE
         WHEN json_extract(extra_json, '$.hours_to_resolution') < 1   THEN '<1h'
         WHEN json_extract(extra_json, '$.hours_to_resolution') < 4   THEN '1-4h'
         WHEN json_extract(extra_json, '$.hours_to_resolution') < 24  THEN '4-24h'
         WHEN json_extract(extra_json, '$.hours_to_resolution') < 72  THEN '1-3d'
         WHEN json_extract(extra_json, '$.hours_to_resolution') < 168 THEN '3-7d'
         ELSE '7d+'
       END AS horizon,
       COUNT(*) AS n,
       SUM(won) AS w,
       ROUND(100.0*SUM(won)/COUNT(*), 1) AS wr_pct,
       ROUND(SUM(realized_pnl), 2) AS pnl
FROM kalshi_round_trips
WHERE strategy='kalshi_crypto_arb'
  AND entry_ts > '2026-04-01'
  AND market_result IN ('yes','no')
GROUP BY horizon
ORDER BY MIN(json_extract(extra_json, '$.hours_to_resolution'));
SQL

echo ""
echo "===== G. Skip reasons from audit_event (last 7 days) ====="
sqlite3 -header -column $DB <<'SQL'
SELECT kind, COUNT(*) AS n
FROM audit_event
WHERE actor='kalshi_crypto_arb'
  AND ts > date('now', '-7 days')
  AND kind LIKE 'kalshi_crypto_skipped_%'
GROUP BY kind
ORDER BY n DESC
LIMIT 15;
SQL

echo ""
echo "===== H. Open positions still alive ====="
sqlite3 -header -column $DB <<'SQL'
SELECT 'open_unresolved' AS metric, COUNT(*) AS n
  FROM audit_event
 WHERE actor='kalshi_crypto_arb'
   AND kind='would_have_placed'
   AND COALESCE(json_extract(payload_json,'$.side'),'buy')='buy'
   AND json_extract(payload_json,'$.order_id') NOT IN (
     SELECT entry_order_id FROM kalshi_round_trips WHERE entry_order_id IS NOT NULL
   );
SELECT 'oldest_open_ts' AS metric, MIN(ts) AS n
  FROM audit_event
 WHERE actor='kalshi_crypto_arb'
   AND kind='would_have_placed'
   AND COALESCE(json_extract(payload_json,'$.side'),'buy')='buy'
   AND json_extract(payload_json,'$.order_id') NOT IN (
     SELECT entry_order_id FROM kalshi_round_trips WHERE entry_order_id IS NOT NULL
   );
SQL
