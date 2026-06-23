#!/usr/bin/env bash
# READ-ONLY. Bitunix's strategies.yaml COLLISION GUARD for the PEAD bundle (hard
# requirement #1). Run AFTER PEAD's additive robinhood_pead edit, BEFORE restart.
# ABORTS (exit 9) if the live fee-coupled values / kill switch were reverted, or
# if PEAD's edit changed ANY line inside the bitunix_futures block.
#
# Usage: bitunix_preserve_check.sh [pre-PEAD-backup]
#   $1 = the backup PEAD's apply made (e.g. strategies.yaml.bak-pre-pead-*).
#        If omitted, auto-finds the newest *.bak-pre-pead*; the byte-identical
#        block diff is skipped if no backup is found (value asserts still run).
set -uo pipefail
CFG="${TC_CFG:-/home/azureuser/trading_corp/config/strategies.yaml}"
bx(){ awk -v k="$1" '/^bitunix_futures:/{f=1} f&&$0~k{print;exit} /^[a-z]/&&!/^bitunix_futures:/{f=0}' "$CFG"; }
block(){ awk '/^bitunix_futures:/{f=1} /^[a-z]/&&!/^bitunix_futures:/{f=0} f' "$1"; }
fail=0
echo "=== Bitunix strategies.yaml preserve-check (read-only) ==="
bx taker_pct | grep -q 'taker_pct: 0\.00019' && echo "  taker_pct 0.00019 OK" \
  || { echo "  FAIL: taker_pct != 0.00019 — FEE-COUPLED REVERTED"; fail=1; }
bx tp1_min_profit_multiplier | grep -q 'tp1_min_profit_multiplier: 3\.75' && echo "  tp1_min_profit_multiplier 3.75 OK" \
  || { echo "  FAIL: tp1_min_profit_multiplier != 3.75 — FEE-COUPLED REVERTED"; fail=1; }
bx auto_execute | grep -q 'auto_execute:' && echo "  auto_execute kill switch present OK" \
  || { echo "  FAIL: auto_execute key missing"; fail=1; }
bx snapshot_staleness_threshold_seconds | grep -q 'snapshot_staleness' && echo "  staleness gate present OK" \
  || echo "  WARN: staleness key not matched in block — verify manually"
BAK="${1:-$(ls -t "$CFG".bak-pre-pead* 2>/dev/null | head -1)}"
if [ -n "${BAK:-}" ] && [ -f "$BAK" ]; then
  if diff <(block "$BAK") <(block "$CFG") >/dev/null; then
    echo "  bitunix_futures block BYTE-IDENTICAL pre/post PEAD OK"
  else
    echo "  FAIL: PEAD edit changed lines INSIDE the bitunix_futures block:"; diff <(block "$BAK") <(block "$CFG"); fail=1
  fi
else
  echo "  (no pre-PEAD backup found — block-identity diff skipped; pass it as \$1)"
fi
[ "$fail" = "0" ] && echo "PRESERVE-CHECK PASS" \
  || { echo "PRESERVE-CHECK FAIL — ABORT bundle apply, restore backup, do NOT restart"; exit 9; }
