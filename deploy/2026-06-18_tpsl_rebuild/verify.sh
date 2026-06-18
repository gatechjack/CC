#!/usr/bin/env bash
# READ-ONLY post-restart VERIFY. sqlite ro. No writes.
set -uo pipefail
cd /home/azureuser/trading_corp
DB="file:data/trading_corp.db?mode=ro"

echo "=== A1. engine state (PID must != 2926399) ==="
systemctl show trading-corp -p MainPID -p ActiveState -p SubState -p NRestarts -p ExecMainStartTimestamp

echo "=== A2. deploy-set md5 (== target 74aa1b42/19da15ff/707c6828) ==="
md5sum trading_corp/brokers/bitunix.py trading_corp/agents/divisions/bitunix_futures_observer.py trading_corp/agents/divisions/bitunix_position_reconciler.py

echo "=== A3. main.py/db.py md5 (== f16e9c24 / a2c2ff46 unchanged) ==="
md5sum trading_corp/main.py trading_corp/persistence/db.py

echo "=== A4. ExecStart re-arm ==="
systemctl cat trading-corp | grep ExecStart

echo "=== boot journal: broker registrations + bitunix connect + reconciler start (6m) ==="
journalctl -u trading-corp --since "6 minutes ago" --no-pager 2>/dev/null | grep -iE "Registered .* broker|BitunixBroker connected|reconciler: starting|stale" | tail -25

echo "=== boot journal: ERROR/CRITICAL/Traceback (6m) ==="
journalctl -u trading-corp --since "6 minutes ago" --no-pager 2>/dev/null | grep -iE "ERROR|CRITICAL|Traceback|Exception|404|30038" | tail -20

echo "=== config files present ==="
ls -1 strategies.yaml risk.yaml config/strategies.yaml config/risk.yaml 2>/dev/null

echo "=== config: bitunix_futures block (execution_mode / maker / B2 / post_only) ==="
SF=$(ls strategies.yaml config/strategies.yaml 2>/dev/null | head -1)
awk '/^[[:space:]]*bitunix_futures:/{f=1} f{print} f&&/^[[:alnum:]_]+:/&&!/bitunix_futures:/{exit}' "$SF" 2>/dev/null | grep -iE "execution_mode|maker|b2|post_only" | head -20

echo "=== config: risk DD-cap (per_account_max_drawdown_pct, expect 0.99 for bitunix) ==="
RF=$(ls risk.yaml config/risk.yaml 2>/dev/null | head -1)
grep -niE "drawdown|bitunix" "$RF" 2>/dev/null | head -25

echo "=== A: position count (0=flat) + recent reconcile/halt/orphan (6m) ==="
sqlite3 "$DB" "SELECT COUNT(*) FROM position;"
journalctl -u trading-corp --since "6 minutes ago" --no-pager 2>/dev/null | grep -iE "reconcile_position_state|_halt_new_orders|orphan" | tail -8

echo "=== liveness: recent scan cycles (freeze-fix proxy) ==="
journalctl -u trading-corp --since "4 minutes ago" --no-pager 2>/dev/null | grep -iE "scan_cycle|scanner:|emitted|enumerated" | tail -6
