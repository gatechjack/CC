#!/usr/bin/env bash
# Bitunix deploy batch 2026-06-16 — STEP 4 post-restart verification (READ-ONLY).
set -uo pipefail
ROOT=/home/azureuser/trading_corp
md5of(){ md5sum "$1" 2>/dev/null | cut -d' ' -f1; }

echo "===== (1) ENGINE: new PID / NRestarts / state / start time ====="
systemctl show trading-corp -p MainPID -p NRestarts -p ActiveState -p SubState -p ActiveEnterTimestamp 2>/dev/null
echo "(pre-restart PID was 2727670 — MainPID must DIFFER and ActiveState=active/running)"

echo
echo "===== (2) 6 .py prod md5==TARGET (loaded code on disk) ====="
declare -A T=(
[trading_corp/brokers/bitunix.py]=70f7904f676e9dd76b1f8ef384226e66
[trading_corp/brokers/base.py]=a7886843d52a6ba74fb0eb6e5a9c0bcd
[trading_corp/agents/divisions/bitunix_futures_observer.py]=3067a3e9d979624dca040657632dd1ba
[trading_corp/agents/divisions/bitunix_position_reconciler.py]=bf048cd14f11cd2b1c5a91bd6b4c0f1d
[trading_corp/brokers/bitunix_exceptions.py]=363b044e6c87489b138fa8a489296d14
[trading_corp/agents/strategies/trade_plan.py]=67f0ff2b3edc32d6f007f3fdfdff5d40
)
for rel in "${!T[@]}"; do
  m=$(md5of "$ROOT/$rel")
  if [ "$m" = "${T[$rel]}" ]; then echo "OK   TARGET  $rel"; else echo "MISMATCH $rel prod=$m want=${T[$rel]}"; fi
done

echo
echo "===== (3) startup audit from THIS boot (mode / paper / brokers) ====="
journalctl -u trading-corp --no-pager -n 600 2>/dev/null | grep -iE "mode=LIVE|Registered bitunix.*broker|live_brokers|dry_run|Web command center initialized" | tail -6

echo
echo "===== (4) strategies.yaml live + maker OFF (loaded config file) ====="
"$ROOT/venv/bin/python" - "$ROOT/config/strategies.yaml" <<'PYEOF'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
bx = d["bitunix_futures"]; fees = bx.get("fees", {})
print("execution_mode =", repr(bx.get("execution_mode")), "(expect 'live')")
print("maker_entry_enabled =", repr(fees.get("maker_entry_enabled")), "(expect False — maker OFF)")
PYEOF

echo
echo "===== (5) risk.yaml DD-cap (expect bitunix override 0.99, untouched) ====="
grep -nE "per_account_max_drawdown_pct|bitunix_futures" "$ROOT/config/risk.yaml" | head -8

echo
echo "===== (6) reconciler on new boot (startup tick / halt / divergence) ====="
journalctl -u trading-corp --no-pager -n 600 2>/dev/null | grep -iE "bitunix_position_reconciler|reconcile_position_state|_halt_new_orders|DIVERGENCE" | tail -8

echo
echo "===== (7) error scan since restart (last 600) ====="
echo "--- tracebacks / rejects / CRITICAL ---"
journalctl -u trading-corp --no-pager -n 600 2>/dev/null | grep -iE "Traceback|live_order_rejected|CRITICAL|flatten_account" | tail -8
echo "--- 10006 count in last 600 lines (fix should reduce churn) ---"
echo "10006 lines: $(journalctl -u trading-corp --no-pager -n 600 2>/dev/null | grep -c '10006')"
echo
echo "===== STEP 4 VERIFY DONE ====="
