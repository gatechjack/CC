#!/usr/bin/env bash
# Bitunix deploy batch 2026-06-16 — PRE-FLIGHT (READ-ONLY). No writes, no restart.
# Streamed to prod and run there. Re-confirms state before staging.
set -uo pipefail
ROOT=/home/azureuser/trading_corp
md5of(){ md5sum "$1" 2>/dev/null | cut -d' ' -f1; }

echo "===== (a) ENGINE STATE ====="
systemctl show trading-corp -p MainPID -p NRestarts -p ActiveState -p SubState 2>/dev/null
echo "--- startup audit (mode / live_brokers / paper) ---"
journalctl -u trading-corp --no-pager 2>/dev/null | grep -iE "mode=LIVE|live_brokers|dry_run|paper=False|execution_mode" | tail -5
echo "--- reconciler (last ticks) ---"
journalctl -u trading-corp --no-pager 2>/dev/null | grep -E "position_state_reconciled" | tail -3
echo "--- halt state (last halt-related lines) ---"
journalctl -u trading-corp --no-pager 2>/dev/null | grep -iE "_halt_new_orders|halt_new_orders|halt cleared|halt_clear" | tail -3
echo "--- recent errors (tracebacks / rejects) ---"
journalctl -u trading-corp --no-pager -n 400 2>/dev/null | grep -iE "Traceback|live_order_rejected|10006|CRITICAL" | tail -5

echo
echo "===== (b) 6 DEPLOY .py md5 vs BASE ====="
declare -A BASE=(
[trading_corp/brokers/bitunix.py]=64d857246a0879c4378e5b3a4185874e
[trading_corp/brokers/base.py]=68d40f230f5a7937f7837cccde960eb1
[trading_corp/agents/divisions/bitunix_futures_observer.py]=e30f17565bff0132aba215568eb8b8f5
[trading_corp/agents/divisions/bitunix_position_reconciler.py]=ae2fbc74895d5b4341f0d2d0804579c1
[trading_corp/brokers/bitunix_exceptions.py]=4c78ebca522818c27c5acbe7806e8314
[trading_corp/agents/strategies/trade_plan.py]=74b9b9def4e8a3f1434f40ef5a69183f
)
for rel in "${!BASE[@]}"; do
  m=$(md5of "$ROOT/$rel")
  if [ "$m" = "${BASE[$rel]}" ]; then echo "OK   BASE-match  $rel"; else echo "DRIFT $rel  prod=$m  expected_BASE=${BASE[$rel]}"; fi
done

echo
echo "===== (c) strategies.yaml execution_mode:live + kalshi disabled ====="
"$ROOT/venv/bin/python" - "$ROOT/config/strategies.yaml" <<'PYEOF'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
bx = d.get("bitunix_futures", {})
print("bitunix_futures.execution_mode =", repr(bx.get("execution_mode")))
print("maker_entry_enabled present? =", "maker_entry_enabled" in bx.get("fees", {}))
k = d.get("kalshi") or d.get("kalshi_crypto") or {}
print("kalshi.enabled =", repr(k.get("enabled")) if k else "(no kalshi block at top key)")
PYEOF
echo "--- raw kalshi enabled greps ---"
grep -nE "^kalshi|enabled:" "$ROOT/config/strategies.yaml" | grep -iE "kalshi|enabled" | head -8

echo
echo "===== (d) risk.yaml DD-cap (expect 0.99) ====="
grep -nE "per_account_max_drawdown_pct|bitunix_futures" "$ROOT/config/risk.yaml" | head -10

echo
echo "===== (e) data_exec.py md5 (EXCLUDED from deploy — just reporting) ====="
echo "data_exec.py prod md5 = $(md5of "$ROOT/trading_corp/agents/data_exec.py")  (expect e3e4cca7... ; NOT in deploy set)"
echo
echo "===== PRE-FLIGHT DONE (read-only) ====="
