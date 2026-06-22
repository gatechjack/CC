#!/usr/bin/env bash
# Fee COUPLED correction post-apply VERIFY — READ-ONLY.
ROOT="${TC_ROOT:-/home/azureuser/trading_corp}"
CFG="$ROOT/config/strategies.yaml"
bxget() { awk -v k="$1" '/^bitunix_futures:/{f=1} f&&$0 ~ k{print "  "$0; exit} /^[a-z]/&&!/^bitunix_futures:/{f=0}' "$CFG"; }
echo "=== fee-coupled VERIFY (read-only) ==="
echo "--- bitunix taker_pct (want 0.00019) ---"; bxget "taker_pct"
echo "--- bitunix tp1_min_profit_multiplier (want 3.75) ---"; bxget "tp1_min_profit_multiplier"
echo "--- fee_floor identity: 3.75 x (0.00019+0.00019+0.0001) = $(python3 -c 'print(round(3.75*0.00048,6))') ; baseline 2.0 x 0.0009 = 0.0018 (must match) ---"
echo "--- other divisions UNCHANGED (kalshi taker_pct still 0.0004, mult still 2.0) ---"
grep -nE "taker_pct|tp1_min_profit_multiplier" "$CFG" | head
echo "--- yaml parses? ---"; python3 -c "import yaml; yaml.safe_load(open('$CFG')); print('  yaml OK')" 2>&1 | tail -1
echo "=== reminder ==="
echo "Restart needed to load (FeeConfig + StrategyConfig read at startup)."
echo "Post-restart: gate behavior is IDENTICAL to before (same fee_floor 0.0018), but on the"
echo "TRUE rate. The 183-trade net-negative cohort stays SKIPPED. fees_too_high_for_risk"
echo "skip-rate should be ~unchanged vs pre-apply (honesty fix, not a behavior change)."
