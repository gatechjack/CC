#!/bin/bash
DB=/home/azureuser/trading_corp/data/trading_corp.db
echo "===== A. Total + max entry_ts ====="
sqlite3 -header -column $DB <<'SQL'
SELECT COUNT(*) AS total,
       MIN(entry_ts) AS first_ts,
       MAX(entry_ts) AS last_ts,
       MAX(resolved_ts) AS last_resolved
  FROM kalshi_round_trips
 WHERE strategy='kalshi_crypto_arb';
SQL

echo ""
echo "===== B. Distinct extra_json keys (top-level) ====="
sqlite3 $DB <<'SQL'
SELECT DISTINCT json_each.key, COUNT(*) AS n
  FROM kalshi_round_trips, json_each(kalshi_round_trips.extra_json)
 WHERE strategy='kalshi_crypto_arb'
 GROUP BY json_each.key
 ORDER BY n DESC
 LIMIT 30;
SQL

echo ""
echo "===== C. Anything that looks like inactive/archived/historical flag ====="
sqlite3 $DB <<'SQL'
SELECT DISTINCT json_each.key, json_each.value
  FROM kalshi_round_trips, json_each(kalshi_round_trips.extra_json)
 WHERE strategy='kalshi_crypto_arb'
   AND (json_each.key LIKE '%active%' OR json_each.key LIKE '%hist%'
        OR json_each.key LIKE '%archiv%' OR json_each.key LIKE '%depreca%'
        OR json_each.key LIKE '%legacy%' OR json_each.key LIKE '%status%'
        OR json_each.key LIKE '%version%' OR json_each.key LIKE '%logic%')
 LIMIT 20;
SQL

echo ""
echo "===== D. Recent strategy-related git/config + audit kinds last 14 days ====="
sqlite3 -header -column $DB <<'SQL'
SELECT substr(ts, 1, 10) AS day, kind, COUNT(*) AS n
  FROM audit_event
 WHERE actor='kalshi_crypto_arb'
   AND kind IN ('strategy_config_change','kalshi_crypto_arb_inactive_marked',
                'kalshi_crypto_historical_archived','strategy_reset')
   AND ts > date('now','-14 days')
 GROUP BY day, kind
 ORDER BY day DESC;
SQL

echo ""
echo "===== E. arb_type breakdown (sometimes used to discriminate) ====="
sqlite3 -header -column $DB <<'SQL'
SELECT COALESCE(arb_type,'(null)') AS arb_type,
       COUNT(*) AS n,
       MIN(entry_ts) AS first_ts,
       MAX(entry_ts) AS last_ts
  FROM kalshi_round_trips
 WHERE strategy='kalshi_crypto_arb'
 GROUP BY arb_type
 ORDER BY n DESC;
SQL

echo ""
echo "===== F. Other tables that might hold an inactive flag ====="
sqlite3 $DB "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%kalshi%' OR name LIKE '%inactive%' OR name LIKE '%archived%';"

echo ""
echo "===== G. Sample 3 most-recent kalshi_crypto_arb round_trips (full extra_json) ====="
sqlite3 $DB <<'SQL'
SELECT entry_ts, won, realized_pnl, extra_json
  FROM kalshi_round_trips
 WHERE strategy='kalshi_crypto_arb'
 ORDER BY entry_ts DESC
 LIMIT 3;
SQL
