#!/usr/bin/env bash
# D3 post-apply VERIFY — READ-ONLY.
ROOT="${TC_ROOT:-/home/azureuser/trading_corp}"
B="$ROOT/trading_corp/brokers/bitunix.py"
R="$ROOT/trading_corp/agents/divisions/bitunix_position_reconciler.py"
echo "=== D3 VERIFY (read-only) ==="
echo "--- md5 (want bitunix 4b00dea2... / reconciler 8c3adcd1...) ---"
md5sum "$B" "$R" | awk '{print $1"  "$2}'
echo "--- D3 markers present? ---"
grep -q 'placed_role' "$B" && echo "  bitunix.py: placed_role (order-semantics entry role) present" || echo "  MISSING placed_role"
grep -q 'D3_TAKER_FEE_REF' "$R" && echo "  reconciler: D3 fee-ref constants present" || echo "  MISSING D3 constants"
grep -q 'role_fee_mismatch' "$R" && echo "  reconciler: role_fee_mismatch corroboration present" || echo "  MISSING role_fee_mismatch"
grep -q 'roleType' "$R" && echo "  NOTE: 'roleType' still textually present in reconciler? (should be 0 in role path)" || echo "  reconciler: no roleType in role path"
echo "--- both compile? ---"
python3 -c "import py_compile; py_compile.compile('$B',doraise=True); py_compile.compile('$R',doraise=True); print('  py_compile OK (both)')" 2>&1 | tail -1
echo "=== reminder ==="
echo "Restart needed to LOAD (both are .py, imported at startup). Flat-window."
echo "Post-restart: next stop-out should book exit_role=taker (not maker); a market"
echo "entry books entry_role=taker; role_fee_mismatch=false when role and fee agree."
echo "BACKFILL of the 2 mis-labelled records is SEPARATE (operator-gated)."
