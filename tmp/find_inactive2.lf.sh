#!/bin/bash
DB=/home/azureuser/trading_corp/data/trading_corp.db

echo "===== A. Custom audit kinds in last 7d (anything that mentions inactive/historical/archive/reset) ====="
sqlite3 -header -column $DB <<'SQL'
SELECT kind, COUNT(*) AS n, MAX(ts) AS last_ts
  FROM audit_event
 WHERE ts > date('now','-7 days')
   AND (kind LIKE '%inactive%' OR kind LIKE '%histor%' OR kind LIKE '%archiv%'
        OR kind LIKE '%reset%' OR kind LIKE '%deactiv%' OR kind LIKE '%retire%'
        OR kind LIKE '%v2%' OR kind LIKE '%logic_change%')
 GROUP BY kind
 ORDER BY last_ts DESC LIMIT 30;
SQL

echo ""
echo "===== B. agent_state slots for kalshi_crypto_arb ====="
sqlite3 -header -column $DB <<'SQL'
SELECT agent, key, length(value_json) AS bytes, updated_ts
  FROM agent_state
 WHERE agent='kalshi_crypto_arb' OR agent LIKE '%kalshi_crypto%'
 ORDER BY updated_ts DESC;
SQL

echo ""
echo "===== C. Entries by hour to find a recent gap (when the cutover happened) ====="
sqlite3 -header -column $DB <<'SQL'
SELECT substr(entry_ts, 1, 13) AS hour, COUNT(*) AS n
  FROM kalshi_round_trips
 WHERE strategy='kalshi_crypto_arb'
 GROUP BY hour
 ORDER BY hour DESC
 LIMIT 30;
SQL

echo ""
echo "===== D. kalshi_round_trips full column list ====="
sqlite3 $DB ".schema kalshi_round_trips" | head -40

echo ""
echo "===== E. Any 'inactive' or similar in extra_json across whole table ====="
sqlite3 $DB <<'SQL'
SELECT json_each.key, COUNT(*) AS n
  FROM kalshi_round_trips, json_each(kalshi_round_trips.extra_json)
 WHERE json_each.key NOT IN ('subtitle','risk_verdict','risk_reason','rationale',
                              'llm_reasoning','llm_confidence','leg',
                              'key_unknowns','expires_at')
 GROUP BY json_each.key
 ORDER BY n DESC LIMIT 30;
SQL

echo ""
echo "===== F. Most recent 'kalshi_crypto_arb' would_have_placed audits ====="
sqlite3 -separator ' | ' $DB <<'SQL'
SELECT ts, json_extract(payload_json,'$.side'),
       json_extract(payload_json,'$.symbol'),
       json_extract(payload_json,'$.divergence_pct')
  FROM audit_event
 WHERE actor='kalshi_crypto_arb'
   AND kind='would_have_placed'
 ORDER BY ts DESC LIMIT 5;
SQL
