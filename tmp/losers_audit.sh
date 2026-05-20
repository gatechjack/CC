#!/bin/bash
DB=/home/azureuser/trading_corp/data/trading_corp.db
CUTOFF='2026-05-16T19:37:00+00:00'

echo "===== A. All 13 losing trades post-cutoff (full detail) ====="
sqlite3 -separator $'\t' $DB <<SQL
SELECT
  substr(entry_ts, 12, 8) AS entry_t,
  substr(resolved_ts, 12, 8) AS res_t,
  ticker,
  outcome_bet AS bet,
  market_result AS res,
  ROUND(entry_price, 3) AS px_in,
  ROUND(implied_at_entry, 3) AS impl,
  ROUND(llm_prob, 3) AS p_yes,
  ROUND(divergence_pct, 1) AS div_pct,
  ROUND(realized_pnl, 2) AS pnl,
  json_extract(extra_json, '$.rationale') AS rationale
FROM kalshi_round_trips
WHERE strategy='kalshi_crypto_arb'
  AND entry_ts >= '$CUTOFF'
  AND won=0
  AND market_result IN ('yes','no')
ORDER BY entry_ts;
SQL

echo ""
echo "===== B. Side x outcome (who was wrong, by what?) ====="
sqlite3 -header -column $DB <<SQL
SELECT outcome_bet AS bet,
       market_result AS res,
       COUNT(*) AS n,
       ROUND(AVG(implied_at_entry), 3) AS avg_impl,
       ROUND(AVG(llm_prob), 3) AS avg_p_yes,
       ROUND(AVG(divergence_pct), 1) AS avg_div_pct
FROM kalshi_round_trips
WHERE strategy='kalshi_crypto_arb'
  AND entry_ts >= '$CUTOFF'
  AND market_result IN ('yes','no')
GROUP BY outcome_bet, market_result;
SQL

echo ""
echo "===== C. Time-to-expiry on the 13 losers (entry → resolve gap) ====="
sqlite3 -header -column $DB <<SQL
SELECT
  ticker,
  ROUND((julianday(resolved_ts) - julianday(entry_ts)) * 24, 2) AS hrs_to_resolve,
  outcome_bet AS bet,
  market_result AS res,
  ROUND(implied_at_entry, 3) AS impl,
  ROUND(divergence_pct, 1) AS div
FROM kalshi_round_trips
WHERE strategy='kalshi_crypto_arb'
  AND entry_ts >= '$CUTOFF'
  AND won=0
  AND market_result IN ('yes','no')
ORDER BY hrs_to_resolve;
SQL

echo ""
echo "===== D. Same data for the 48 WINNERS — for comparison ====="
sqlite3 -header -column $DB <<SQL
SELECT
  CASE outcome_bet WHEN 'yes' THEN 'YES->' WHEN 'no' THEN 'NO->' END
  || market_result AS pattern,
  COUNT(*) AS n_win,
  ROUND(AVG(implied_at_entry), 3) AS avg_impl_in,
  ROUND(AVG((julianday(resolved_ts) - julianday(entry_ts))*24), 1) AS avg_hrs_to_resolve
FROM kalshi_round_trips
WHERE strategy='kalshi_crypto_arb'
  AND entry_ts >= '$CUTOFF'
  AND won=1
GROUP BY pattern;
SQL
