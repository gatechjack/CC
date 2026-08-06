#!/usr/bin/env bash
# Polymarket arbitrage edge eval - PHASE 2: full slices + calibration + concentration.
# READ-ONLY sqlite3 -readonly. No writes, no sudo. Clean cohort =
#   polymarket_round_trips, COALESCE(division,'polymarket_arbitrage')='polymarket_arbitrage',
#   entry_ts >= '2026-05-21T12:28:07'  (n=272 confirmed in Phase 1).
# Heredocs are quoted (<<'SQL') so bash does not touch json_extract $. paths or SQL quotes.
set -u
DB=/home/azureuser/trading_corp/data/trading_corp.db

echo "=== P2.1 overall clean stats + significance inputs (compute stddev/t offline) ==="
sqlite3 -readonly "$DB" <<'SQL'
.headers on
.mode column
SELECT COUNT(*)                        AS n,
       SUM(won)                        AS wins,
       ROUND(100.0*SUM(won)/COUNT(*),2) AS wr_pct,
       ROUND(SUM(realized_pnl),4)      AS total_pnl,
       ROUND(AVG(realized_pnl),6)      AS avg_pnl,
       ROUND(AVG(realized_pnl*realized_pnl),6) AS avg_pnl_sq,
       ROUND(SUM(notional),4)          AS total_notional,
       ROUND(SUM(CASE WHEN outcome_bet='yes' THEN implied_at_entry ELSE 1.0-implied_at_entry END),3) AS exp_wins_mkt,
       ROUND(SUM(CASE WHEN outcome_bet='yes' THEN llm_prob        ELSE 1.0-llm_prob        END),3) AS exp_wins_llm
FROM polymarket_round_trips
WHERE COALESCE(division,'polymarket_arbitrage')='polymarket_arbitrage'
  AND entry_ts >= '2026-05-21T12:28:07';
SQL

echo "=== P2.2 by category (n, wins, WR%, total_pnl, avg_pnl) ==="
sqlite3 -readonly "$DB" <<'SQL'
.headers on
.mode column
SELECT COALESCE(category,'(null)')     AS category,
       COUNT(*)                        AS n,
       SUM(won)                        AS wins,
       ROUND(100.0*SUM(won)/COUNT(*),1) AS wr_pct,
       ROUND(SUM(realized_pnl),3)      AS total_pnl,
       ROUND(AVG(realized_pnl),4)      AS avg_pnl
FROM polymarket_round_trips
WHERE COALESCE(division,'polymarket_arbitrage')='polymarket_arbitrage'
  AND entry_ts >= '2026-05-21T12:28:07'
GROUP BY category ORDER BY n DESC;
SQL

echo "=== P2.3 by llm_prob bucket (0-20..80-100) ==="
sqlite3 -readonly "$DB" <<'SQL'
.headers on
.mode column
SELECT CASE WHEN llm_prob<0.20 THEN '1:00-20'
            WHEN llm_prob<0.40 THEN '2:20-40'
            WHEN llm_prob<0.60 THEN '3:40-60'
            WHEN llm_prob<0.80 THEN '4:60-80'
            ELSE '5:80-100' END        AS llm_bucket,
       COUNT(*)                        AS n,
       SUM(won)                        AS wins,
       ROUND(100.0*SUM(won)/COUNT(*),1) AS wr_pct,
       ROUND(SUM(realized_pnl),3)      AS total_pnl,
       ROUND(AVG(realized_pnl),4)      AS avg_pnl
FROM polymarket_round_trips
WHERE COALESCE(division,'polymarket_arbitrage')='polymarket_arbitrage'
  AND entry_ts >= '2026-05-21T12:28:07'
GROUP BY llm_bucket ORDER BY llm_bucket;
SQL

echo "=== P2.4 by side (outcome_bet) ==="
sqlite3 -readonly "$DB" <<'SQL'
.headers on
.mode column
SELECT outcome_bet                     AS side,
       COUNT(*)                        AS n,
       SUM(won)                        AS wins,
       ROUND(100.0*SUM(won)/COUNT(*),1) AS wr_pct,
       ROUND(SUM(realized_pnl),3)      AS total_pnl,
       ROUND(AVG(realized_pnl),4)      AS avg_pnl
FROM polymarket_round_trips
WHERE COALESCE(division,'polymarket_arbitrage')='polymarket_arbitrage'
  AND entry_ts >= '2026-05-21T12:28:07'
GROUP BY outcome_bet ORDER BY n DESC;
SQL

echo "=== P2.5 by entry_price bucket (cents; the price of the side actually bet) ==="
sqlite3 -readonly "$DB" <<'SQL'
.headers on
.mode column
SELECT CASE WHEN entry_price<0.20 THEN '1:00-20'
            WHEN entry_price<0.40 THEN '2:20-40'
            WHEN entry_price<0.60 THEN '3:40-60'
            WHEN entry_price<0.80 THEN '4:60-80'
            ELSE '5:80-100' END        AS px_bucket,
       COUNT(*)                        AS n,
       SUM(won)                        AS wins,
       ROUND(100.0*SUM(won)/COUNT(*),1) AS wr_pct,
       ROUND(SUM(realized_pnl),3)      AS total_pnl,
       ROUND(AVG(realized_pnl),4)      AS avg_pnl
FROM polymarket_round_trips
WHERE COALESCE(division,'polymarket_arbitrage')='polymarket_arbitrage'
  AND entry_ts >= '2026-05-21T12:28:07'
GROUP BY px_bucket ORDER BY px_bucket;
SQL

echo "=== P2.6 calibration: llm_prob bucket vs actual YES rate (yes_won) ==="
sqlite3 -readonly "$DB" <<'SQL'
.headers on
.mode column
SELECT CASE WHEN llm_prob<0.20 THEN '1:00-20'
            WHEN llm_prob<0.40 THEN '2:20-40'
            WHEN llm_prob<0.60 THEN '3:40-60'
            WHEN llm_prob<0.80 THEN '4:60-80'
            ELSE '5:80-100' END        AS llm_bucket,
       COUNT(*)                        AS n,
       ROUND(AVG(llm_prob),3)          AS avg_llm_yesprob,
       ROUND(AVG(implied_at_entry),3)  AS avg_mkt_yesprob,
       ROUND(AVG(yes_won),3)           AS actual_yes_rate
FROM polymarket_round_trips
WHERE COALESCE(division,'polymarket_arbitrage')='polymarket_arbitrage'
  AND entry_ts >= '2026-05-21T12:28:07'
GROUP BY llm_bucket ORDER BY llm_bucket;
SQL

echo "=== P2.7 Brier: LLM vs market-implied (YES basis; lower=better) + split by who was picked ==="
sqlite3 -readonly "$DB" <<'SQL'
.headers on
.mode column
SELECT COUNT(*)                                              AS n,
       ROUND(AVG((llm_prob-yes_won)*(llm_prob-yes_won)),5)          AS brier_llm,
       ROUND(AVG((implied_at_entry-yes_won)*(implied_at_entry-yes_won)),5) AS brier_mkt,
       ROUND(AVG((0.5-yes_won)*(0.5-yes_won)),5)                    AS brier_coinflip,
       ROUND(AVG(yes_won),3)                                        AS base_yes_rate
FROM polymarket_round_trips
WHERE COALESCE(division,'polymarket_arbitrage')='polymarket_arbitrage'
  AND entry_ts >= '2026-05-21T12:28:07';
SQL

echo "=== P2.8 correlated-underlying: series with >1 distinct condition_id in clean set ==="
sqlite3 -readonly "$DB" <<'SQL'
.headers on
.mode column
SELECT COALESCE(NULLIF(series,''),'(none)') AS series,
       COUNT(*)                    AS n_trades,
       COUNT(DISTINCT condition_id) AS n_distinct_cid,
       SUM(won)                    AS wins,
       ROUND(SUM(realized_pnl),3)  AS total_pnl
FROM polymarket_round_trips
WHERE COALESCE(division,'polymarket_arbitrage')='polymarket_arbitrage'
  AND entry_ts >= '2026-05-21T12:28:07'
GROUP BY series ORDER BY n_trades DESC;
SQL

echo "=== P2.9 clean-set condition_id re-entry: distinct cids + top by trade count ==="
sqlite3 -readonly "$DB" <<'SQL'
.headers on
.mode column
SELECT COUNT(*) AS total_clean, COUNT(DISTINCT condition_id) AS distinct_cids
FROM polymarket_round_trips
WHERE COALESCE(division,'polymarket_arbitrage')='polymarket_arbitrage'
  AND entry_ts >= '2026-05-21T12:28:07';
SQL
sqlite3 -readonly "$DB" <<'SQL'
.headers on
.mode column
SELECT substr(condition_id,1,14) AS cid, COUNT(*) AS n_clean_trades,
       ROUND(SUM(realized_pnl),3) AS total_pnl, MAX(slug) AS a_slug
FROM polymarket_round_trips
WHERE COALESCE(division,'polymarket_arbitrage')='polymarket_arbitrage'
  AND entry_ts >= '2026-05-21T12:28:07'
GROUP BY condition_id HAVING COUNT(*)>1 ORDER BY n_clean_trades DESC LIMIT 20;
SQL

echo "=== P2.10 dedupe-skip concentration: top 20 condition_ids by skip count since epoch ==="
sqlite3 -readonly "$DB" <<'SQL'
.headers on
.mode column
SELECT substr(json_extract(payload_json,'$.condition_id'),1,14) AS cid,
       COUNT(*)                                            AS skips,
       MAX(CAST(json_extract(payload_json,'$.current_open_count') AS INTEGER)) AS max_open_seen,
       MAX(json_extract(payload_json,'$.category'))        AS category,
       substr(MAX(json_extract(payload_json,'$.market_question')),1,50) AS question
FROM audit_event
WHERE actor='polymarket_arbitrage' AND kind='polymarket_dedupe_skipped'
  AND ts >= '2026-05-21T12:28:07'
GROUP BY json_extract(payload_json,'$.condition_id')
ORDER BY skips DESC LIMIT 20;
SQL

echo "=== P2.11 dedupe skips by category (context) ==="
sqlite3 -readonly "$DB" <<'SQL'
.headers on
.mode column
SELECT COALESCE(json_extract(payload_json,'$.category'),'(null)') AS category,
       COUNT(*) AS skips,
       COUNT(DISTINCT json_extract(payload_json,'$.condition_id')) AS distinct_cids
FROM audit_event
WHERE actor='polymarket_arbitrage' AND kind='polymarket_dedupe_skipped'
  AND ts >= '2026-05-21T12:28:07'
GROUP BY category ORDER BY skips DESC;
SQL

echo "=== P2 DONE ==="
