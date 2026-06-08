#!/usr/bin/env bash
# Thread A round 1, call 2 (az-split): A2 + A2b (gate-stack localization).
# READ-ONLY. SELECT only. No writes.
set -uo pipefail
DB=/home/azureuser/trading_corp/data/trading_corp.db

echo "=== A2: gate-stack event counts/day since 2026-06-04 (bitunix/confluence/htf-tagged) ==="
sqlite3 -header -column "$DB" "SELECT DATE(ts) AS day, kind, COUNT(*) AS count FROM audit_event WHERE ts>='2026-06-04T00:00:00+00:00' AND (kind LIKE 'webhook_%' OR kind LIKE 'alert_%' OR kind LIKE '%decision%' OR kind LIKE '%skipped%' OR kind LIKE '%rejected%' OR kind='would_have_placed' OR kind='agent_error') AND (payload_json LIKE '%bitunix%' OR payload_json LIKE '%confluence%' OR payload_json LIKE '%htf%') GROUP BY day, kind ORDER BY day, count DESC;"
echo

echo "=== A2b: ALL bitunix/confluence-tagged audit kinds since 2026-06-04 (no kind whitelist; catches A2 blind spots) ==="
sqlite3 -header -column "$DB" "SELECT kind, COUNT(*) AS count, MIN(ts) AS first, MAX(ts) AS last FROM audit_event WHERE ts>='2026-06-04T00:00:00+00:00' AND (payload_json LIKE '%bitunix%' OR payload_json LIKE '%confluence%') GROUP BY kind ORDER BY count DESC;"
echo "=== END call 2 (read-only) ==="
