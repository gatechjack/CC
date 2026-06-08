#!/usr/bin/env bash
# Thread A round 1, call 1 (az-split): connectivity + A1 + A5 (small/priority output).
# READ-ONLY. SELECT only. No writes.
set -uo pipefail
DB=/home/azureuser/trading_corp/data/trading_corp.db

echo "=== CONNECTIVITY / CONTEXT ==="
echo "host=$(hostname) now_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if [ -f "$DB" ]; then echo "db=present size_bytes=$(stat -c %s "$DB")"; else echo "db=MISSING at $DB"; fi
echo

echo "=== A1: bitunix_futures trades/day since 2026-06-02 (expect rows 06-02/06-03, then silence) ==="
sqlite3 -header -column "$DB" "SELECT DATE(ts) AS day, COUNT(*) AS trades FROM paper_trade_record WHERE division='bitunix_futures' AND ts>='2026-06-02T00:00:00+00:00' GROUP BY day ORDER BY day;"
echo

echo "=== A5: Phase 3 live-mode primitives firing in paper-mode since deploy (HARD-STOP if ANY rows) ==="
sqlite3 -header -column "$DB" "SELECT kind, COUNT(*) AS count, MIN(ts) AS first, MAX(ts) AS last FROM audit_event WHERE ts>='2026-06-02T01:39:00+00:00' AND (kind LIKE 'live_exit_order_%' OR kind LIKE 'position_state_%' OR kind LIKE 'restart_resume_%' OR kind IN ('exit_outcome_recorded','orphan_broker_position_on_restart')) GROUP BY kind;"
echo "=== END call 1 (read-only) ==="
