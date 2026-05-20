#!/bin/bash
echo "===== nojnn in any slot? ====="
sqlite3 -separator ' | ' /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT 'watch_only_whales' AS slot,
       (SELECT COUNT(*) FROM agent_state, json_each(value_json) AS j
         WHERE agent='polymarket_copy_trader' AND key='watch_only_whales'
           AND json_extract(j.value, '$.user_name')='nojnn') AS hits;
SELECT 'selected_whales' AS slot,
       (SELECT COUNT(*) FROM agent_state, json_each(value_json) AS j
         WHERE agent='polymarket_copy_trader' AND key='selected_whales'
           AND json_extract(j.value, '$.user_name')='nojnn') AS hits;
SELECT 'pinned_whales' AS slot,
       (SELECT COUNT(*) FROM agent_state, json_each(value_json) AS j
         WHERE agent='polymarket_copy_trader' AND key='pinned_whales'
           AND json_extract(j.value, '$.user_name')='nojnn') AS hits;
SQL

echo ""
echo "===== watch_only_whales size + sample wallets ====="
sqlite3 -separator ' | ' /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT json_array_length(value_json)
  FROM agent_state
 WHERE agent='polymarket_copy_trader' AND key='watch_only_whales';
SQL

echo ""
echo "===== Currently in selected (post-demote) ====="
sqlite3 -separator ' | ' /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT json_extract(j.value, '$.user_name') AS name,
       json_extract(j.value, '$.wallet') AS wallet,
       json_extract(j.value, '$.source') AS source
  FROM agent_state, json_each(agent_state.value_json) AS j
 WHERE agent_state.agent='polymarket_copy_trader'
   AND agent_state.key='selected_whales';
SQL

echo ""
echo "===== Audit timeline for nojnn ====="
sqlite3 -separator ' | ' /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
SELECT ts, kind, json_extract(payload_json, '$.user_name'), json_extract(payload_json, '$.source')
  FROM audit_event
 WHERE (json_extract(payload_json, '$.user_name')='nojnn'
        OR json_extract(payload_json, '$.wallet')='0x7f9e2d1df78614564a70becc7fa14aa9a6623a0e')
   AND kind IN ('polymarket_whale_promoted','polymarket_whale_demoted')
 ORDER BY ts ASC;
SQL

echo ""
echo "===== Other manually-promoted whales: are they in watch_only_whales? ====="
sqlite3 -header -column /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
WITH pinned AS (
  SELECT json_extract(j.value, '$.user_name') AS name,
         lower(json_extract(j.value, '$.wallet')) AS wallet
    FROM agent_state, json_each(agent_state.value_json) AS j
   WHERE agent_state.agent='polymarket_copy_trader'
     AND agent_state.key='pinned_whales'
),
wo AS (
  SELECT lower(json_extract(j.value, '$.proxy_wallet')) AS wallet
    FROM agent_state, json_each(agent_state.value_json) AS j
   WHERE agent_state.agent='polymarket_copy_trader'
     AND agent_state.key='watch_only_whales'
)
SELECT pinned.name, pinned.wallet,
       CASE WHEN pinned.wallet IN (SELECT wallet FROM wo) THEN 'in_watch_only' ELSE 'MISSING' END AS status
  FROM pinned;
SQL
