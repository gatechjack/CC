#!/usr/bin/env bash
# FIX SMOKE TEST (report-only, NON-PLACING). Proves the deployed code carries Fix 1/2/3.
set +e
APPROOT=/home/azureuser/trading_corp; cd "$APPROOT"
PY="$APPROOT/venv/bin/python"
echo "=== deterministic module-level check (imports the SAME deployed code) ==="
PYTHONUTF8=1 "$PY" -X utf8 - <<'PYEOF'
from trading_corp.web import pmcc_pricing
print("Fix2 market_regular_open =", pmcc_pricing.market_regular_open())
print("Fix2 MARKET_CLOSED_REASON =", repr(pmcc_pricing.MARKET_CLOSED_REASON))
print("Fix2 market_closed_extras =", pmcc_pricing.market_closed_extras())
try:
    from trading_corp.agents.divisions.pmcc_robinhood import PMCCAgent
    a = PMCCAgent()  # default config paths (cwd=APPROOT)
    okA, rA = a._passes_liquidity({"bid":4.90,"ask":5.05,"open_interest":209,"volume":0})
    print("Fix1 highOI(209)/vol0  -> pass=", okA, "| reason=", rA, "(EXPECT pass=True)")
    okB, rB = a._passes_liquidity({"bid":3.70,"ask":3.80,"open_interest":73,"volume":454})
    print("Fix1 thin OI73/vol454  -> pass=", okB, "| reason=", rB, "(EXPECT pass=False, liveness)")
    okC, rC = a._passes_liquidity({"bid":1.00,"ask":1.50,"open_interest":500,"volume":1000})
    print("Fix1 wide spread       -> pass=", okC, "| reason=", rC, "(EXPECT pass=False, spread)")
    print("Fix3 last_roll_abort_reason present =", hasattr(a, "last_roll_abort_reason"))
    print("Fix1 _min_avg_volume removed =", not hasattr(type(a), "_min_avg_volume"))
except Exception as e:
    print("agent-level check error (non-fatal):", type(e).__name__, e)
PYEOF
echo ""
echo "=== HTTP refresh-pricing smoke on held symbols (LLM-free; market closed -> expect honest message) ==="
for S in TSLA RKLB HOOD; do
  R=$(curl -s -m 15 -X POST "http://127.0.0.1:8000/division/robinhood_pmcc/pair/$S/refresh-pricing" 2>&1)
  MC=$(echo "$R" | grep -oiE "market closed[^<]{0,60}9:30[^<]{0,20}" | head -1)
  OLD=$(echo "$R" | grep -ociE "market closed, illiquid, or a sparse chain")
  echo "$S: honest_market_closed_msg=[${MC:-none}] | OLD_conflated_present=$OLD | bytes=${#R}"
done
echo "END_SMOKE"
