set -u
DB=/home/azureuser/trading_corp/data/trading_corp.db
RO="sqlite3 -readonly $DB"
echo "=== SELECTED_WHALES (CURRENT copy list) — user|wallet|cat|source|promoted ==="
$RO "SELECT json_extract(je.value,'\$.user_name')||'|'||json_extract(je.value,'\$.wallet')||'|'||json_extract(je.value,'\$.category')||'|'||json_extract(je.value,'\$.source')||'|'||json_extract(je.value,'\$.promoted_iso') FROM agent_state a, json_each(a.value_json) je WHERE a.agent='polymarket_copy_trader' AND a.key='selected_whales';"
echo "=== selected count ==="
$RO "SELECT COUNT(*) FROM agent_state a, json_each(a.value_json) je WHERE a.agent='polymarket_copy_trader' AND a.key='selected_whales';"
echo "=== DONE ==="
