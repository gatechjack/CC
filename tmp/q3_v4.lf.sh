#!/bin/bash
echo "===== A. agent_state slot sizes ====="
sqlite3 -header -column /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT agent, key,
       json_array_length(value_json) AS list_len,
       updated_ts
  FROM agent_state
 WHERE agent IN ('polymarket_copy_trader','kalshi_copy_trader')
   AND key IN ('watch_only_whales','selected_whales','pinned_whales')
 ORDER BY agent, key;
SQL

echo ""
echo "===== B. Top 5 watchlist whales ====="
sqlite3 -separator ' | ' /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
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
echo "===== C. Pinned PM whales ====="
sqlite3 -separator ' | ' /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT json_extract(j.value, '$.user_name'),
       json_extract(j.value, '$.proxy_wallet'),
       json_extract(j.value, '$.promoted_iso')
  FROM agent_state, json_each(agent_state.value_json) AS j
 WHERE agent_state.agent='polymarket_copy_trader'
   AND agent_state.key='pinned_whales'
 LIMIT 30;
SQL

echo ""
echo "===== D. Pinned Kalshi whales ====="
sqlite3 -separator ' | ' /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT json_extract(j.value, '$') FROM agent_state, json_each(agent_state.value_json) AS j
 WHERE agent_state.agent='kalshi_copy_trader' AND agent_state.key='pinned_whales' LIMIT 30;
SQL

echo ""
echo "===== E. PM round_trips since 17:18 UTC, by synthetic_close flag ====="
sqlite3 -header -column /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT 'pm_total_since_17_18' AS x, COUNT(*) AS n FROM polymarket_round_trips
 WHERE entry_ts > '2026-05-17T17:18' OR resolved_ts > '2026-05-17T17:18';
SELECT 'pm_w_synth_close'      AS x, COUNT(*) AS n FROM polymarket_round_trips
 WHERE json_extract(extra_json, '$.is_synthetic_close')=1;
SELECT 'pm_with_demoted_via_ui' AS x, COUNT(*) AS n FROM polymarket_round_trips
 WHERE json_extract(extra_json, '$.synthetic_close_reason')='demoted_via_ui';
SELECT 'pm_division_breakdown' AS x, division AS n, COUNT(*) AS c
  FROM polymarket_round_trips
 WHERE entry_ts > '2026-05-17T17:18' OR resolved_ts > '2026-05-17T17:18'
 GROUP BY division;
SQL

echo ""
echo "===== F. KS round_trips since 17:18 UTC, by synthetic_close flag ====="
sqlite3 -header -column /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT 'ks_total_since_17_18' AS x, COUNT(*) AS n FROM kalshi_round_trips
 WHERE entry_ts > '2026-05-17T17:18' OR resolved_ts > '2026-05-17T17:18';
SELECT 'ks_w_synth_close'      AS x, COUNT(*) AS n FROM kalshi_round_trips
 WHERE json_extract(extra_json, '$.is_synthetic_close')=1;
SQL

echo ""
echo "===== G. Sample of recent PM round_trips ====="
sqlite3 -separator ' | ' /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT entry_ts, resolved_ts, division, slug, substr(extra_json, 1, 60)
  FROM polymarket_round_trips
 WHERE entry_ts > '2026-05-17T17:18' OR resolved_ts > '2026-05-17T17:18'
 ORDER BY resolved_ts DESC
 LIMIT 6;
SQL

echo ""
echo "===== H. Resolver / synthetic-SELL audit pairing check ====="
sqlite3 -header -column /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT 'synth_sell_audits_pm' AS x, COUNT(*) AS n FROM audit_event
 WHERE kind='would_have_placed'
   AND json_extract(payload_json, '$.is_synthetic_close')=1
   AND json_extract(payload_json, '$.strategy')='polymarket_copy_trader';
SELECT 'synth_sell_audits_ks' AS x, COUNT(*) AS n FROM audit_event
 WHERE kind='would_have_placed'
   AND json_extract(payload_json, '$.is_synthetic_close')=1
   AND json_extract(payload_json, '$.strategy')='kalshi_copy_trader';
SELECT 'cold_start_audits_pm' AS x, COUNT(*) AS n FROM audit_event
 WHERE kind='polymarket_copy_cold_start' AND ts > '2026-05-17T17:18';
SQL
