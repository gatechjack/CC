#!/usr/bin/env bash
# Thread B call 1: trading-corp service state history + healthz. READ-ONLY (systemctl show / curl).
# HARD-STOP GATE: NRestarts>0 OR MainPID != 2043009 => unplanned restart since 2026-06-02 deploy.
# Expected baseline (deploy_log 2026-06-02): MainPID=2043009, NRestarts=0,
#   ActiveEnterTimestamp = Tue 2026-06-02 01:39:50 UTC, ExecMainStart ~01:39:55Z.
set -uo pipefail

echo "=== CONTEXT ==="
echo "host=$(hostname) whoami=$(whoami) now_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

echo "=== B1: trading-corp service state ==="
systemctl show trading-corp --property=MainPID,ActiveState,SubState,NRestarts,ActiveEnterTimestamp,ExecMainStartTimestamp,ExecMainStatus,InvocationID,Result
echo

echo "=== healthz (public URL via Caddy) ==="
curl -sS -m 15 -o /dev/null -w "healthz=%{http_code}\n" https://trading.jacksumner.com/healthz || echo "healthz=CURL_FAILED"
echo "=== END call B1 (read-only) ==="
