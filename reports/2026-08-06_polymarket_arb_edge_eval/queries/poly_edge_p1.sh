#!/usr/bin/env bash
# Polymarket arbitrage edge eval - PHASE 1: n>=50 gate + schema/format sanity.
# READ-ONLY. Uses sqlite3 -readonly per runbooks/session_start_2026_05_22_polymarket_post_cap.md.
# No writes, no sudo. DB is owned by azureuser.
# Clean-data epoch (cap deploy): 2026-05-21T12:28:07 UTC.
set -u
DB=/home/azureuser/trading_corp/data/trading_corp.db
EPOCH='2026-05-21T12:28:07'

echo "=== P1.0 table row counts (round_trips total, audit poly total) ==="
sqlite3 -readonly "$DB" "SELECT 'polymarket_round_trips_total', COUNT(*) FROM polymarket_round_trips;"
sqlite3 -readonly "$DB" "SELECT 'audit_poly_total', COUNT(*) FROM audit_event WHERE actor='polymarket_arbitrage';"

echo "=== P1.1 distinct division values in round_trips (copy-trader isolation check) ==="
sqlite3 -readonly "$DB" "SELECT COALESCE(division,'(null)') AS division, COUNT(*) AS n FROM polymarket_round_trips GROUP BY division ORDER BY n DESC;"

echo "=== P1.2 entry_ts format (min, max, 3 newest samples) ==="
sqlite3 -readonly "$DB" "SELECT MIN(entry_ts) AS min_ts, MAX(entry_ts) AS max_ts FROM polymarket_round_trips;"
sqlite3 -readonly "$DB" "SELECT entry_ts FROM polymarket_round_trips ORDER BY entry_ts DESC LIMIT 3;"

echo "=== P1.3 THE GATE: clean resolved n / wins / WR% / total PnL / PnL-per-trade ==="
sqlite3 -readonly "$DB" "SELECT COUNT(*) AS clean_resolved, SUM(won) AS wins, ROUND(100.0*SUM(won)/COUNT(*),1) AS wr_pct, ROUND(SUM(realized_pnl),2) AS total_pnl, ROUND(SUM(realized_pnl)/COUNT(*),4) AS pnl_per_trade FROM polymarket_round_trips WHERE COALESCE(division,'polymarket_arbitrage')='polymarket_arbitrage' AND entry_ts >= '$EPOCH';"

echo "=== P1.4 clean set: arb rows (entry_order_id NULL) vs copy-trader leak (set) ==="
sqlite3 -readonly "$DB" "SELECT CASE WHEN entry_order_id IS NULL THEN 'arb_entry_order_id_NULL' ELSE 'has_entry_order_id' END AS kind, COUNT(*) AS n FROM polymarket_round_trips WHERE COALESCE(division,'polymarket_arbitrage')='polymarket_arbitrage' AND entry_ts >= '$EPOCH' GROUP BY 1;"

echo "=== P1.5 distinct categories in clean set (spelling check) ==="
sqlite3 -readonly "$DB" "SELECT COALESCE(category,'(null)') AS category, COUNT(*) AS n FROM polymarket_round_trips WHERE COALESCE(division,'polymarket_arbitrage')='polymarket_arbitrage' AND entry_ts >= '$EPOCH' GROUP BY category ORDER BY n DESC;"

echo "=== P1.6 llm_prob + implied_at_entry scale/nulls (expect 0..1) ==="
sqlite3 -readonly "$DB" "SELECT ROUND(MIN(llm_prob),4) AS llm_min, ROUND(MAX(llm_prob),4) AS llm_max, ROUND(AVG(llm_prob),4) AS llm_avg, SUM(CASE WHEN llm_prob IS NULL THEN 1 ELSE 0 END) AS llm_null, ROUND(MIN(implied_at_entry),4) AS impl_min, ROUND(MAX(implied_at_entry),4) AS impl_max, SUM(CASE WHEN implied_at_entry IS NULL THEN 1 ELSE 0 END) AS impl_null FROM polymarket_round_trips WHERE COALESCE(division,'polymarket_arbitrage')='polymarket_arbitrage' AND entry_ts >= '$EPOCH';"

echo "=== P1.7 entry_price range + outcome_bet distribution ==="
sqlite3 -readonly "$DB" "SELECT ROUND(MIN(entry_price),4) AS px_min, ROUND(MAX(entry_price),4) AS px_max, SUM(CASE WHEN outcome_bet='yes' THEN 1 ELSE 0 END) AS yes_n, SUM(CASE WHEN outcome_bet='no' THEN 1 ELSE 0 END) AS no_n, SUM(CASE WHEN outcome_bet NOT IN ('yes','no') OR outcome_bet IS NULL THEN 1 ELSE 0 END) AS other_n FROM polymarket_round_trips WHERE COALESCE(division,'polymarket_arbitrage')='polymarket_arbitrage' AND entry_ts >= '$EPOCH';"

echo "=== P1.8 dedupe skips since epoch (total count) ==="
sqlite3 -readonly "$DB" "SELECT COUNT(*) AS dedupe_skips FROM audit_event WHERE actor='polymarket_arbitrage' AND kind='polymarket_dedupe_skipped' AND ts >= '$EPOCH';"

echo "=== P1.9 pre-cap vs post-cap resolved counts (context) ==="
sqlite3 -readonly "$DB" "SELECT SUM(CASE WHEN entry_ts < '$EPOCH' THEN 1 ELSE 0 END) AS pre_cap, SUM(CASE WHEN entry_ts >= '$EPOCH' THEN 1 ELSE 0 END) AS post_cap FROM polymarket_round_trips WHERE COALESCE(division,'polymarket_arbitrage')='polymarket_arbitrage';"

echo "=== P1 DONE ==="
