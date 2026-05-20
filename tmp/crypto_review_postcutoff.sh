#!/bin/bash
DB=/home/azureuser/trading_corp/data/trading_corp.db
CUTOFF='2026-05-16T19:37:00+00:00'

echo "===== A. Headline (post-cutoff) ====="
sqlite3 -header -column $DB <<SQL
SELECT
  COUNT(*) AS n_round_trips,
  ROUND(SUM(realized_pnl), 2) AS pnl,
  ROUND(AVG(realized_pnl), 3) AS avg_pnl,
  SUM(won) AS wins,
  SUM(CASE WHEN won=0 AND market_result IN ('yes','no') THEN 1 ELSE 0 END) AS losses,
  SUM(CASE WHEN market_result='void' THEN 1 ELSE 0 END) AS voids,
  ROUND(SUM(notional), 0) AS notional
FROM kalshi_round_trips
WHERE strategy='kalshi_crypto_arb'
  AND entry_ts >= '$CUTOFF';
SQL

echo ""
echo "===== B. Divergence-bucket calibration (post-cutoff) ====="
sqlite3 -header -column $DB <<SQL
WITH bucketed AS (
  SELECT CASE
           WHEN divergence_pct < 15 THEN '10-15%'
           WHEN divergence_pct < 20 THEN '15-20%'
           WHEN divergence_pct < 30 THEN '20-30%'
           WHEN divergence_pct < 50 THEN '30-50%'
           ELSE '50%+'
         END AS div_bucket,
         won, realized_pnl, notional
  FROM kalshi_round_trips
  WHERE strategy='kalshi_crypto_arb'
    AND entry_ts >= '$CUTOFF'
    AND market_result IN ('yes','no')
)
SELECT div_bucket,
       COUNT(*) AS n,
       SUM(won) AS w,
       ROUND(100.0*SUM(won)/COUNT(*), 1) AS wr_pct,
       ROUND(SUM(realized_pnl), 2) AS pnl,
       ROUND(100.0*SUM(realized_pnl)/SUM(notional), 1) AS roi_pct
FROM bucketed
GROUP BY div_bucket
ORDER BY div_bucket;
SQL

echo ""
echo "===== C. yes vs no side (post-cutoff) ====="
sqlite3 -header -column $DB <<SQL
SELECT outcome_bet,
       COUNT(*) AS n,
       SUM(won) AS w,
       ROUND(100.0*SUM(won)/COUNT(*), 1) AS wr_pct,
       ROUND(SUM(realized_pnl), 2) AS pnl,
       ROUND(AVG(implied_at_entry), 3) AS avg_implied
FROM kalshi_round_trips
WHERE strategy='kalshi_crypto_arb'
  AND entry_ts >= '$CUTOFF'
  AND market_result IN ('yes','no')
GROUP BY outcome_bet;
SQL

echo ""
echo "===== D. PnL by event_ticker (post-cutoff) ====="
sqlite3 -header -column $DB <<SQL
SELECT event_ticker,
       COUNT(*) AS n,
       SUM(won) AS w,
       ROUND(SUM(realized_pnl), 2) AS pnl,
       ROUND(AVG(divergence_pct), 1) AS avg_div,
       ROUND(AVG(implied_at_entry), 3) AS avg_impl
FROM kalshi_round_trips
WHERE strategy='kalshi_crypto_arb'
  AND entry_ts >= '$CUTOFF'
GROUP BY event_ticker
ORDER BY pnl ASC;
SQL

echo ""
echo "===== E. Bet side × divergence bucket cross-tab ====="
sqlite3 -header -column $DB <<SQL
SELECT outcome_bet,
       CASE
         WHEN divergence_pct < 15 THEN '10-15%'
         WHEN divergence_pct < 20 THEN '15-20%'
         WHEN divergence_pct < 30 THEN '20-30%'
         WHEN divergence_pct < 50 THEN '30-50%'
         ELSE '50%+'
       END AS div_bucket,
       COUNT(*) AS n,
       SUM(won) AS w,
       ROUND(100.0*SUM(won)/COUNT(*), 1) AS wr_pct,
       ROUND(SUM(realized_pnl), 2) AS pnl
FROM kalshi_round_trips
WHERE strategy='kalshi_crypto_arb'
  AND entry_ts >= '$CUTOFF'
  AND market_result IN ('yes','no')
GROUP BY outcome_bet, div_bucket
ORDER BY outcome_bet, div_bucket;
SQL

echo ""
echo "===== F. Per-asset (BTC, ETH, etc.) ====="
sqlite3 -header -column $DB <<SQL
SELECT substr(ticker, 1, instr(ticker, '-')-1) AS asset_root,
       COUNT(*) AS n,
       SUM(won) AS w,
       ROUND(100.0*SUM(won)/COUNT(*), 1) AS wr_pct,
       ROUND(SUM(realized_pnl), 2) AS pnl,
       ROUND(AVG(divergence_pct), 1) AS avg_div
FROM kalshi_round_trips
WHERE strategy='kalshi_crypto_arb'
  AND entry_ts >= '$CUTOFF'
  AND market_result IN ('yes','no')
GROUP BY asset_root
ORDER BY pnl ASC;
SQL

echo ""
echo "===== G. Open positions post-cutoff (still in audit_event, not yet round_trip) ====="
sqlite3 -header -column $DB <<SQL
SELECT 'open_post_cutoff' AS metric,
       COUNT(*) AS n
  FROM audit_event
 WHERE actor='kalshi_crypto_arb'
   AND kind='would_have_placed'
   AND COALESCE(json_extract(payload_json,'$.side'),'buy')='buy'
   AND ts >= '$CUTOFF'
   AND json_extract(payload_json,'$.order_id') NOT IN (
     SELECT entry_order_id FROM kalshi_round_trips WHERE entry_order_id IS NOT NULL
   );
SQL
