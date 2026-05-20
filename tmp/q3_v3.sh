#!/bin/bash
echo "===== agent_state slots (polymarket + kalshi) ====="
sqlite3 -header -column /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT agent, key,
       CASE WHEN json_valid(value_json)
            THEN COALESCE(json_array_length(value_json), 0)
            ELSE -1 END AS list_len,
       length(value_json) AS bytes,
       updated_ts AS updated_utc
  FROM agent_state
 WHERE agent IN ('polymarket_copy_trader','kalshi_copy_trader')
   AND key IN ('watch_only_whales','selected_whales','pinned_whales')
 ORDER BY agent, key;
SQL

echo ""
echo "===== Top 5 watchlist whales (sanity) ====="
sqlite3 -separator $'\t' /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT json_extract(j.value, '$.user_name'),
       json_extract(j.value, '$.win_rate_pct'),
       json_extract(j.value, '$.positions_closed'),
       printf('$%.0fK', json_extract(j.value, '$.realized_pnl_usdc')/1000.0)
  FROM agent_state, json_each(agent_state.value_json) AS j
 WHERE agent_state.agent='polymarket_copy_trader'
   AND agent_state.key='watch_only_whales'
 ORDER BY json_extract(j.value, '$.realized_pnl_usdc') DESC
 LIMIT 5;
SQL

echo ""
echo "===== Promote/demote audit detail w/ ts as TEXT ====="
sqlite3 -header -column /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT ts AS utc,
       kind,
       json_extract(payload_json, '$.handle')       AS k_handle,
       json_extract(payload_json, '$.user_name')    AS pm_name,
       json_extract(payload_json, '$.reason')       AS reason
  FROM audit_event
 WHERE kind IN ('polymarket_whale_promoted','polymarket_whale_demoted',
                'kalshi_whale_promoted','kalshi_whale_demoted')
 ORDER BY ts DESC
 LIMIT 20;
SQL

echo ""
echo "===== Synthetic-close audits by hour ====="
sqlite3 -header -column /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT substr(ts, 1, 13) AS hour_utc,
       json_extract(payload_json, '$.strategy') AS strategy,
       COUNT(*) AS n
  FROM audit_event
 WHERE json_extract(payload_json, '$.is_synthetic_close')=1
 GROUP BY hour_utc, strategy
 ORDER BY hour_utc DESC
 LIMIT 20;
SQL

echo ""
echo "===== Round-trip table schemas ====="
sqlite3 /home/azureuser/trading_corp/data/trading_corp.db ".schema polymarket_round_trips"
echo "---"
sqlite3 /home/azureuser/trading_corp/data/trading_corp.db ".schema kalshi_round_trips"

echo ""
echo "===== Round-trip rows since 17:18 UTC ====="
sqlite3 -header -column /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT 'pm_total' AS x, COUNT(*) AS n FROM polymarket_round_trips
 WHERE opened_ts > '2026-05-17T17:00';
SELECT 'pm_w_synth_close' AS x, COUNT(*) AS n FROM polymarket_round_trips
 WHERE closed_ts IS NOT NULL AND json_extract(extra_json, '$.is_synthetic_close')=1;
SELECT 'pm_recent_closes' AS x, COUNT(*) AS n FROM polymarket_round_trips
 WHERE closed_ts > '2026-05-17T17:00';
SELECT 'pm_open' AS x, COUNT(*) AS n FROM polymarket_round_trips
 WHERE closed_ts IS NULL;
SQL

echo ""
echo "===== Sample of recent round_trips (likely closed by demote) ====="
sqlite3 -header -column /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT opened_ts, closed_ts, wallet, market_slug,
       substr(extra_json, 1, 80) AS extra_preview
  FROM polymarket_round_trips
 WHERE closed_ts > '2026-05-17T17:00'
 ORDER BY closed_ts DESC
 LIMIT 8;
SQL
