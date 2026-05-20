#!/bin/bash
DB=/home/azureuser/trading_corp/data/trading_corp.db

echo "===== A. All 18 KXCHINAANNOUNCE trades (full detail) ====="
sqlite3 -separator $'\t' $DB <<'SQL'
SELECT
  substr(entry_ts, 6, 11) AS entered,
  ticker,
  event_title,
  outcome_bet AS bet,
  market_result AS res,
  ROUND(entry_price, 3) AS px_in,
  ROUND(implied_at_entry, 3) AS impl,
  ROUND(llm_prob, 3) AS p_yes,
  ROUND(divergence_pct, 1) AS div_pct,
  ROUND(realized_pnl, 2) AS pnl
FROM kalshi_round_trips
WHERE strategy='kalshi_llm_arbitrage'
  AND event_ticker='KXCHINAANNOUNCE-26MAY'
ORDER BY entry_ts;
SQL

echo ""
echo "===== B. Distinct tickers + event_title context ====="
sqlite3 -separator ' | ' $DB <<'SQL'
SELECT DISTINCT ticker, event_title
FROM kalshi_round_trips
WHERE strategy='kalshi_llm_arbitrage'
  AND event_ticker='KXCHINAANNOUNCE-26MAY';
SQL

echo ""
echo "===== C. Same-ticker repeats (entered same market multiple times?) ====="
sqlite3 -header -column $DB <<'SQL'
SELECT ticker, outcome_bet, COUNT(*) AS n,
       MIN(entry_ts) AS first_t, MAX(entry_ts) AS last_t
FROM kalshi_round_trips
WHERE strategy='kalshi_llm_arbitrage'
  AND event_ticker='KXCHINAANNOUNCE-26MAY'
GROUP BY ticker, outcome_bet
ORDER BY n DESC;
SQL

echo ""
echo "===== D. Sample LLM rationales (3 wins + 2 losses) ====="
sqlite3 -separator $'\n---\n' $DB <<'SQL'
SELECT
  ticker || ' | bet=' || outcome_bet || ' | res=' || market_result ||
  ' | p_yes=' || ROUND(llm_prob,2) || ' | impl=' || ROUND(implied_at_entry,2)
  || char(10) || COALESCE(json_extract(extra_json, '$.rationale'), '(no rationale)')
FROM kalshi_round_trips
WHERE strategy='kalshi_llm_arbitrage'
  AND event_ticker='KXCHINAANNOUNCE-26MAY'
  AND won=1
ORDER BY entry_ts LIMIT 3;
SQL

echo ""
echo "----- 2 LOSSES -----"
sqlite3 -separator $'\n---\n' $DB <<'SQL'
SELECT
  ticker || ' | bet=' || outcome_bet || ' | res=' || market_result ||
  ' | p_yes=' || ROUND(llm_prob,2) || ' | impl=' || ROUND(implied_at_entry,2)
  || char(10) || COALESCE(json_extract(extra_json, '$.rationale'), '(no rationale)')
FROM kalshi_round_trips
WHERE strategy='kalshi_llm_arbitrage'
  AND event_ticker='KXCHINAANNOUNCE-26MAY'
  AND won=0
ORDER BY entry_ts LIMIT 5;
SQL

echo ""
echo "===== E. Win/loss by bet side + outcome (where the edge lives) ====="
sqlite3 -header -column $DB <<'SQL'
SELECT outcome_bet AS bet,
       market_result AS res,
       COUNT(*) AS n,
       ROUND(SUM(realized_pnl), 2) AS pnl,
       ROUND(AVG(entry_price), 3) AS avg_px,
       ROUND(AVG(implied_at_entry), 3) AS avg_impl,
       ROUND(AVG(llm_prob), 3) AS avg_p
FROM kalshi_round_trips
WHERE strategy='kalshi_llm_arbitrage'
  AND event_ticker='KXCHINAANNOUNCE-26MAY'
GROUP BY bet, res;
SQL

echo ""
echo "===== F. Time-to-resolution + entry timing distribution ====="
sqlite3 -header -column $DB <<'SQL'
SELECT ticker,
       substr(entry_ts, 1, 16) AS entered,
       substr(resolved_ts, 1, 16) AS resolved,
       ROUND((julianday(resolved_ts) - julianday(entry_ts))*24, 1) AS hrs,
       outcome_bet AS bet,
       market_result AS res,
       ROUND(implied_at_entry, 3) AS impl
FROM kalshi_round_trips
WHERE strategy='kalshi_llm_arbitrage'
  AND event_ticker='KXCHINAANNOUNCE-26MAY'
ORDER BY entry_ts LIMIT 25;
SQL
