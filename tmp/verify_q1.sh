#!/usr/bin/env bash
set -u
DB=/home/azureuser/trading_corp/data/trading_corp.db

echo "=== Q1a: PCT pruner audit rows (kind=pct_stale_prune, apply=true, last 3) ==="
sqlite3 -header -column "$DB" <<'SQL'
SELECT ts,
       json_extract(payload_json,'$.apply')      AS apply,
       json_extract(payload_json,'$.candidates') AS candidates,
       json_extract(payload_json,'$.deleted')    AS deleted
  FROM audit_event
 WHERE kind='pct_stale_prune'
   AND json_extract(payload_json,'$.apply')='true'
 ORDER BY ts DESC LIMIT 3;
SQL

echo
echo "=== Q1b: ALL pct_stale_prune audit rows (last 5) ==="
sqlite3 -header -column "$DB" <<'SQL'
SELECT ts,
       json_extract(payload_json,'$.apply')      AS apply,
       json_extract(payload_json,'$.candidates') AS candidates,
       json_extract(payload_json,'$.deleted')    AS deleted
  FROM audit_event
 WHERE kind='pct_stale_prune'
 ORDER BY ts DESC LIMIT 5;
SQL

echo
echo "=== Q1c: PCT would_have_placed count ==="
sqlite3 -header -column "$DB" <<'SQL'
SELECT COUNT(*) AS pct_pending_count
  FROM audit_event
 WHERE actor='polymarket_copy_trader' AND kind='would_have_placed';
SQL

echo
echo "=== Q1d: timer status (one-liners) ==="
echo -n "enabled: "; systemctl is-enabled trading-corp-pct-pruner.timer 2>&1
echo -n "active:  "; systemctl is-active  trading-corp-pct-pruner.timer 2>&1
echo "next-fire:"
systemctl list-timers trading-corp-pct-pruner.timer --no-pager 2>&1 | head -3
echo
echo "=== Q1e: kalshi_weather post-cutoff RTs (may not exist yet) ==="
sqlite3 -header -column "$DB" <<'SQL'
SELECT division, COUNT(*) AS n,
       SUM(CASE WHEN won IS NOT NULL THEN 1 ELSE 0 END) AS resolved,
       SUM(CASE WHEN won IS     NULL THEN 1 ELSE 0 END) AS pending
  FROM kalshi_round_trips
 WHERE division='kalshi_weather'
   AND entry_ts >= '2026-05-16T19:18:00+00:00';
SQL

echo
echo "=== DONE ==="
