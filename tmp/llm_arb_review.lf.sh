#!/bin/bash
DB=/home/azureuser/trading_corp/data/trading_corp.db

echo "===== A. Headline ====="
sqlite3 -header -column $DB <<'SQL'
SELECT COUNT(*) AS n,
       SUM(won) AS wins,
       SUM(CASE WHEN won=0 AND market_result IN ('yes','no') THEN 1 ELSE 0 END) AS losses,
       SUM(CASE WHEN market_result='void' THEN 1 ELSE 0 END) AS voids,
       ROUND(100.0*SUM(won)/COUNT(*), 1) AS wr_pct,
       ROUND(SUM(realized_pnl), 2) AS pnl,
       ROUND(SUM(notional), 0) AS notional,
       MIN(entry_ts) AS first_ts,
       MAX(entry_ts) AS last_ts
  FROM kalshi_round_trips
 WHERE strategy='kalshi_llm_arbitrage';
SQL

echo ""
echo "===== B. By day (last 30) ====="
sqlite3 -header -column $DB <<'SQL'
SELECT substr(entry_ts, 1, 10) AS day,
       COUNT(*) AS n,
       SUM(won) AS w,
       ROUND(100.0*SUM(won)/COUNT(*),1) AS wr,
       ROUND(SUM(realized_pnl), 2) AS pnl
  FROM kalshi_round_trips
 WHERE strategy='kalshi_llm_arbitrage'
 GROUP BY day
 ORDER BY day DESC
 LIMIT 30;
SQL

echo ""
echo "===== C. Divergence calibration ====="
sqlite3 -header -column $DB <<'SQL'
WITH rt AS (
  SELECT CASE
           WHEN divergence_pct < 15 THEN '10-15%'
           WHEN divergence_pct < 20 THEN '15-20%'
           WHEN divergence_pct < 30 THEN '20-30%'
           WHEN divergence_pct < 50 THEN '30-50%'
           ELSE '50%+'
         END AS bucket,
         won, realized_pnl, notional
    FROM kalshi_round_trips
   WHERE strategy='kalshi_llm_arbitrage'
     AND market_result IN ('yes','no')
)
SELECT bucket,
       COUNT(*) AS n,
       SUM(won) AS w,
       ROUND(100.0*SUM(won)/COUNT(*),1) AS wr,
       ROUND(SUM(realized_pnl), 2) AS pnl,
       ROUND(100.0*SUM(realized_pnl)/SUM(notional),1) AS roi_pct
  FROM rt
 GROUP BY bucket
 ORDER BY bucket;
SQL

echo ""
echo "===== D. yes vs no side ====="
sqlite3 -header -column $DB <<'SQL'
SELECT outcome_bet,
       COUNT(*) AS n,
       SUM(won) AS w,
       ROUND(100.0*SUM(won)/COUNT(*),1) AS wr,
       ROUND(SUM(realized_pnl),2) AS pnl,
       ROUND(AVG(implied_at_entry),3) AS avg_impl,
       ROUND(AVG(llm_prob),3) AS avg_llm_p
  FROM kalshi_round_trips
 WHERE strategy='kalshi_llm_arbitrage'
   AND market_result IN ('yes','no')
 GROUP BY outcome_bet;
SQL

echo ""
echo "===== E. By category ====="
sqlite3 -header -column $DB <<'SQL'
SELECT category,
       COUNT(*) AS n,
       SUM(won) AS w,
       ROUND(100.0*SUM(won)/COUNT(*),1) AS wr,
       ROUND(SUM(realized_pnl),2) AS pnl,
       ROUND(AVG(divergence_pct),1) AS avg_div
  FROM kalshi_round_trips
 WHERE strategy='kalshi_llm_arbitrage'
   AND market_result IN ('yes','no')
 GROUP BY category
 ORDER BY pnl ASC;
SQL

echo ""
echo "===== F. LLM calibration — is llm_prob predictive? ====="
sqlite3 -header -column $DB <<'SQL'
WITH rt AS (
  SELECT CASE
           WHEN llm_prob < 0.20 THEN 'p<0.20'
           WHEN llm_prob < 0.40 THEN '0.20-0.40'
           WHEN llm_prob < 0.60 THEN '0.40-0.60'
           WHEN llm_prob < 0.80 THEN '0.60-0.80'
           ELSE 'p>0.80'
         END AS llm_band,
         outcome_bet, market_result, won, realized_pnl, llm_prob
    FROM kalshi_round_trips
   WHERE strategy='kalshi_llm_arbitrage'
     AND market_result IN ('yes','no')
)
SELECT llm_band,
       COUNT(*) AS n,
       SUM(CASE WHEN market_result='yes' THEN 1 ELSE 0 END) AS actual_yes,
       ROUND(100.0*SUM(CASE WHEN market_result='yes' THEN 1 ELSE 0 END)/COUNT(*),1) AS actual_yes_pct,
       ROUND(AVG(llm_prob)*100,1) AS claimed_p_yes,
       SUM(won) AS our_w,
       ROUND(100.0*SUM(won)/COUNT(*),1) AS our_wr,
       ROUND(SUM(realized_pnl),2) AS pnl
  FROM rt
 GROUP BY llm_band
 ORDER BY MIN(llm_prob);
SQL

echo ""
echo "===== G. Top winning + losing event_tickers ====="
sqlite3 -header -column $DB <<'SQL'
SELECT event_ticker,
       COUNT(*) AS n,
       SUM(won) AS w,
       ROUND(SUM(realized_pnl),2) AS pnl,
       ROUND(AVG(divergence_pct),1) AS avg_div,
       ROUND(AVG(llm_prob),3) AS avg_p
  FROM kalshi_round_trips
 WHERE strategy='kalshi_llm_arbitrage'
   AND market_result IN ('yes','no')
 GROUP BY event_ticker
 HAVING n >= 2
 ORDER BY pnl ASC LIMIT 10;
SQL

echo ""
echo "===== H. Top winning event_tickers ====="
sqlite3 -header -column $DB <<'SQL'
SELECT event_ticker,
       COUNT(*) AS n,
       SUM(won) AS w,
       ROUND(SUM(realized_pnl),2) AS pnl,
       ROUND(AVG(divergence_pct),1) AS avg_div
  FROM kalshi_round_trips
 WHERE strategy='kalshi_llm_arbitrage'
   AND market_result IN ('yes','no')
 GROUP BY event_ticker
 HAVING n >= 2
 ORDER BY pnl DESC LIMIT 10;
SQL

echo ""
echo "===== I. Open positions ====="
sqlite3 -header -column $DB <<'SQL'
SELECT 'open_unresolved' AS metric, COUNT(*) AS n
  FROM audit_event
 WHERE actor='kalshi_llm_arbitrage'
   AND kind='would_have_placed'
   AND COALESCE(json_extract(payload_json,'$.side'),'buy')='buy'
   AND json_extract(payload_json,'$.order_id') NOT IN (
     SELECT entry_order_id FROM kalshi_round_trips WHERE entry_order_id IS NOT NULL
   );
SQL
