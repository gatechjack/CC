#!/bin/bash
echo "===== agent_state schema ====="
sqlite3 /home/azureuser/trading_corp/data/trading_corp.db ".schema agent_state"

echo ""
echo "===== Distinct (agent, key) on polymarket + kalshi ====="
sqlite3 -header -column /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT agent, key,
       CASE WHEN json_valid(value)
            THEN COALESCE(json_array_length(value), 0)
            ELSE -1 END AS list_len,
       length(value) AS bytes,
       datetime(updated_ts, 'unixepoch') AS updated_utc
  FROM agent_state
 WHERE agent IN ('polymarket_copy_trader','kalshi_copy_trader')
 ORDER BY agent, key;
SQL

echo ""
echo "===== Top 3 watchlist whales (sanity) ====="
sqlite3 -separator $'\t' /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT json_extract(value_each.value, '$.user_name'),
       json_extract(value_each.value, '$.win_rate_pct'),
       json_extract(value_each.value, '$.positions_closed'),
       printf('$%.0fK', json_extract(value_each.value, '$.realized_pnl_usdc')/1000.0)
  FROM agent_state, json_each(agent_state.value) AS value_each
 WHERE agent_state.agent='polymarket_copy_trader'
   AND agent_state.key='watch_only_whales'
 ORDER BY json_extract(value_each.value, '$.realized_pnl_usdc') DESC
 LIMIT 5;
SQL

echo ""
echo "===== Promote/demote audit detail (last 20, newest first) ====="
sqlite3 -header -column /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT datetime(ts, 'unixepoch') AS utc,
       kind,
       json_extract(payload_json, '$.handle')      AS k_handle,
       json_extract(payload_json, '$.proxy_wallet') AS p_wallet,
       json_extract(payload_json, '$.user_name')    AS pm_name,
       json_extract(payload_json, '$.reason')       AS reason
  FROM audit_event
 WHERE kind IN ('polymarket_whale_promoted','polymarket_whale_demoted',
                'kalshi_whale_promoted','kalshi_whale_demoted')
 ORDER BY ts DESC
 LIMIT 20;
SQL

echo ""
echo "===== Synthetic-close audit count ====="
sqlite3 -header -column /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT kind,
       json_extract(payload_json, '$.synthetic_close_reason') AS reason,
       COUNT(*) AS n
  FROM audit_event
 WHERE json_extract(payload_json, '$.is_synthetic_close')=1
   AND ts > strftime('%s', '2026-05-17 17:00:00')
 GROUP BY kind, reason;
SQL

echo ""
echo "===== Round-trip rows with is_synthetic_close ====="
sqlite3 -header -column /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT 'polymarket_round_trips' AS table_name, COUNT(*) AS n
  FROM polymarket_round_trips
 WHERE json_extract(extra_json, '$.is_synthetic_close')=1;
SELECT 'kalshi_round_trips' AS table_name, COUNT(*) AS n
  FROM kalshi_round_trips
 WHERE json_extract(extra_json, '$.is_synthetic_close')=1;
SQL
