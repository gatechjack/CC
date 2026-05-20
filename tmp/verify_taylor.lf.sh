#!/bin/bash
echo "===== Recent demote audits (since 19:00 UTC) ====="
sqlite3 -separator ' | ' /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT ts, kind,
       json_extract(payload_json, '$.user_name') AS pm_name,
       json_extract(payload_json, '$.handle') AS ks_handle,
       json_extract(payload_json, '$.n_synthetic_sells') AS n_synth
  FROM audit_event
 WHERE kind IN ('polymarket_whale_demoted','polymarket_whale_promoted',
                'kalshi_whale_demoted','kalshi_whale_promoted')
   AND ts > '2026-05-17T19:00'
 ORDER BY ts ASC;
SQL

echo ""
echo "===== Current slot states ====="
sqlite3 -header -column /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT agent, key, json_array_length(value_json) AS list_len, updated_ts
  FROM agent_state
 WHERE agent IN ('polymarket_copy_trader','kalshi_copy_trader')
   AND key IN ('selected_whales','pinned_whales','watch_only_whales');
SQL

echo ""
echo "===== Current PM selected_whales user_names ====="
sqlite3 -separator ' | ' /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT json_extract(j.value, '$.user_name'),
       json_extract(j.value, '$.wallet'),
       json_extract(j.value, '$.source')
  FROM agent_state, json_each(agent_state.value_json) AS j
 WHERE agent_state.agent='polymarket_copy_trader'
   AND agent_state.key='selected_whales';
SQL

echo ""
echo "===== taylorsversion's open BUY audits ====="
sqlite3 -header -column /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT 'open_buys_pm_audits' AS metric,
       COUNT(*) AS n
  FROM audit_event
 WHERE kind='would_have_placed'
   AND json_extract(payload_json,'$.division')='polymarket_copy_trading'
   AND json_extract(payload_json,'$.side')='buy'
   AND json_extract(payload_json,'$.whale_user_name')='taylorsversion'
   AND json_extract(payload_json,'$.order_id') NOT IN
     (SELECT entry_order_id FROM polymarket_round_trips WHERE entry_order_id IS NOT NULL);
SELECT 'round_trips_taylor' AS metric,
       COUNT(*) AS n
  FROM polymarket_round_trips
 WHERE json_extract(extra_json,'$.whale_user_name')='taylorsversion';
SELECT 'recent_buys_taylor' AS metric,
       COUNT(*) AS n
  FROM audit_event
 WHERE kind='would_have_placed'
   AND json_extract(payload_json,'$.side')='buy'
   AND json_extract(payload_json,'$.whale_user_name')='taylorsversion';
SQL
