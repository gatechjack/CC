#!/usr/bin/env bash
# Fee-rate correction post-apply VERIFY — READ-ONLY.
ROOT="${TC_ROOT:-/home/azureuser/trading_corp}"
CFG="$ROOT/config/strategies.yaml"
echo "=== fee-rate VERIFY (read-only) ==="
echo "--- bitunix fees taker_pct (want 0.00019) ---"
awk '/^bitunix_futures:/{f=1} f&&/taker_pct/{print "  "$0; exit} /^[a-z]/&&!/^bitunix_futures:/{f=0}' "$CFG"
echo "--- other divisions' taker_pct must be UNCHANGED (e.g. kalshi still 0.0004) ---"
grep -nE "taker_pct" "$CFG" | head
echo "--- yaml parses? ---"
python3 -c "import yaml; yaml.safe_load(open('$CFG')); print('  yaml OK')" 2>&1 | tail -1
echo "=== reminder ==="
echo "Loading the corrected FeeConfig needs an engine RESTART (FeeConfig is read at startup)."
echo "Post-restart, round_trip_cost_pct halves (0.0009->~0.00048): the TP1 fee floor + the"
echo "fees_too_high_for_risk gate threshold drop ~2x. Whether to ACCEPT the looser gate is"
echo "Step 2's net-edge call — this change only makes the model's RATE truthful."
