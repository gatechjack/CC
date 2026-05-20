#!/bin/bash
DB=/home/azureuser/trading_corp/data/trading_corp.db
CUTOFF='2026-05-16T19:37:00+00:00'

echo "===== Filter A: skip if hours_to_resolve < 4 ====="
sqlite3 -header -column $DB <<SQL
WITH rt AS (
  SELECT
    won, realized_pnl, notional, ticker, outcome_bet,
    implied_at_entry,
    (julianday(resolved_ts) - julianday(entry_ts)) * 24 AS hrs
  FROM kalshi_round_trips
  WHERE strategy='kalshi_crypto_arb'
    AND entry_ts >= '$CUTOFF'
    AND market_result IN ('yes','no')
)
SELECT 'Baseline (61)' AS scenario,
       COUNT(*) AS n, SUM(won) AS w,
       ROUND(100.0*SUM(won)/COUNT(*),1) AS wr,
       ROUND(SUM(realized_pnl),2) AS pnl FROM rt
UNION ALL
SELECT 'Skip hrs<4', COUNT(*), SUM(won),
       ROUND(100.0*SUM(won)/COUNT(*),1),
       ROUND(SUM(realized_pnl),2) FROM rt WHERE hrs >= 4
UNION ALL
SELECT 'Skip hrs<6', COUNT(*), SUM(won),
       ROUND(100.0*SUM(won)/COUNT(*),1),
       ROUND(SUM(realized_pnl),2) FROM rt WHERE hrs >= 6
UNION ALL
SELECT 'Skip hrs<12', COUNT(*), SUM(won),
       ROUND(100.0*SUM(won)/COUNT(*),1),
       ROUND(SUM(realized_pnl),2) FROM rt WHERE hrs >= 12;
SQL

echo ""
echo "===== Filter B: skip if implied < 0.10 or > 0.90 (extreme R:R) ====="
sqlite3 -header -column $DB <<SQL
WITH rt AS (
  SELECT won, realized_pnl, implied_at_entry, outcome_bet
  FROM kalshi_round_trips
  WHERE strategy='kalshi_crypto_arb'
    AND entry_ts >= '$CUTOFF'
    AND market_result IN ('yes','no')
)
SELECT 'Baseline (61)' AS scenario,
       COUNT(*) AS n, SUM(won) AS w,
       ROUND(100.0*SUM(won)/COUNT(*),1) AS wr,
       ROUND(SUM(realized_pnl),2) AS pnl FROM rt
UNION ALL
SELECT 'Skip impl<0.15 or >0.85',
       COUNT(*), SUM(won),
       ROUND(100.0*SUM(won)/COUNT(*),1),
       ROUND(SUM(realized_pnl),2)
  FROM rt WHERE implied_at_entry BETWEEN 0.15 AND 0.85
UNION ALL
SELECT 'Skip impl<0.10 or >0.90',
       COUNT(*), SUM(won),
       ROUND(100.0*SUM(won)/COUNT(*),1),
       ROUND(SUM(realized_pnl),2)
  FROM rt WHERE implied_at_entry BETWEEN 0.10 AND 0.90;
SQL

echo ""
echo "===== Filter C: skip DOGE ====="
sqlite3 -header -column $DB <<SQL
WITH rt AS (
  SELECT won, realized_pnl, ticker
  FROM kalshi_round_trips
  WHERE strategy='kalshi_crypto_arb'
    AND entry_ts >= '$CUTOFF'
    AND market_result IN ('yes','no')
)
SELECT 'Baseline (61)' AS scenario,
       COUNT(*) AS n, SUM(won) AS w,
       ROUND(100.0*SUM(won)/COUNT(*),1) AS wr,
       ROUND(SUM(realized_pnl),2) AS pnl FROM rt
UNION ALL
SELECT 'No DOGE',
       COUNT(*), SUM(won),
       ROUND(100.0*SUM(won)/COUNT(*),1),
       ROUND(SUM(realized_pnl),2)
  FROM rt WHERE ticker NOT LIKE 'KXDOGE%';
SQL

echo ""
echo "===== Combined: hrs >= 4 AND 0.10 <= impl <= 0.90 ====="
sqlite3 -header -column $DB <<SQL
WITH rt AS (
  SELECT won, realized_pnl, implied_at_entry,
    (julianday(resolved_ts) - julianday(entry_ts)) * 24 AS hrs
  FROM kalshi_round_trips
  WHERE strategy='kalshi_crypto_arb'
    AND entry_ts >= '$CUTOFF'
    AND market_result IN ('yes','no')
)
SELECT 'Baseline' AS scenario,
       COUNT(*) AS n, SUM(won) AS w,
       ROUND(100.0*SUM(won)/COUNT(*),1) AS wr,
       ROUND(SUM(realized_pnl),2) AS pnl FROM rt
UNION ALL
SELECT 'hrs>=4 AND 0.10<=impl<=0.90',
       COUNT(*), SUM(won),
       ROUND(100.0*SUM(won)/COUNT(*),1),
       ROUND(SUM(realized_pnl),2)
  FROM rt WHERE hrs >= 4 AND implied_at_entry BETWEEN 0.10 AND 0.90;
SQL
