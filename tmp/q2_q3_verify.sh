#!/bin/bash
set -e

echo "===== Q2: Weekly timer state ====="
systemctl is-enabled trading-corp-pm-watchlist-deep.timer 2>&1 || echo "NOT-ENABLED"
systemctl is-active trading-corp-pm-watchlist-deep.timer 2>&1 || echo "NOT-ACTIVE"
echo "---- list-timers ----"
systemctl list-timers trading-corp-pm-watchlist-deep.timer --no-pager 2>&1 || echo "FAILED"

echo ""
echo "===== Q2b: Main service health ====="
systemctl is-active trading-corp 2>&1
systemctl show trading-corp -p MainPID 2>&1

echo ""
echo "===== Q3: SQL sanity ====="
sqlite3 /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
.headers on
.mode column
-- (a) Polymarket watchlist slot still populated?
SELECT 'watchlist_slot_count' AS metric, COUNT(*) AS value
  FROM agent_state
 WHERE actor='polymarket_copy_trader' AND key='watch_only_whales';

-- (a2) Size of the watchlist JSON
SELECT 'watchlist_whale_count' AS metric,
       json_array_length(value_json) AS value
  FROM agent_state
 WHERE actor='polymarket_copy_trader' AND key='watch_only_whales';

-- (b) Any promote/demote audits since 17:18 UTC yesterday?
SELECT kind, COUNT(*) AS n
  FROM audit_event
 WHERE kind IN ('polymarket_whale_promoted','polymarket_whale_demoted',
                'kalshi_whale_promoted','kalshi_whale_demoted')
 GROUP BY kind;

-- (c) PCT pending count
SELECT 'pct_pending' AS metric, COUNT(*) AS value
  FROM audit_event
 WHERE actor='polymarket_copy_trader' AND kind='would_have_placed';

-- (d) selected + pinned slots for both venues
SELECT actor, key,
       CASE WHEN json_valid(value_json)
            THEN COALESCE(json_array_length(value_json), 0)
            ELSE -1 END AS list_len
  FROM agent_state
 WHERE (actor='polymarket_copy_trader' AND key IN ('selected_whales','pinned_whales'))
    OR (actor='kalshi_copy_trader'     AND key IN ('selected_whales','pinned_whales','watch_only_whales'));
SQL

echo ""
echo "===== Q3b: Top of the watchlist (sanity) ====="
sqlite3 /home/azureuser/trading_corp/data/trading_corp.db <<'SQL'
.mode list
SELECT json_extract(value, '$.user_name') AS name,
       json_extract(value, '$.win_rate_pct') AS wr,
       json_extract(value, '$.positions_closed') AS positions,
       printf('$%.2fK', json_extract(value, '$.realized_pnl_usdc')/1000.0) AS pnl
  FROM agent_state, json_each(agent_state.value_json)
 WHERE agent_state.actor='polymarket_copy_trader'
   AND agent_state.key='watch_only_whales'
 ORDER BY json_extract(value, '$.realized_pnl_usdc') DESC
 LIMIT 3;
SQL
