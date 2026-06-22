#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
#  D1 post-apply VERIFY — READ-ONLY. No writes, no restart.
#  Run AFTER apply_d1.sh. md5/compile/markers are checkable immediately;
#  the live audit-field check requires a restart + the next netted close.
# ════════════════════════════════════════════════════════════════════════════
ROOT="${TC_ROOT:-/home/azureuser/trading_corp}"
TARGET="$ROOT/trading_corp/agents/divisions/bitunix_position_reconciler.py"
NEW_MD5="5c4c8dba04a267c660c5fe826dabb16c"

echo "=== D1 VERIFY (read-only) ==="
echo "target = $TARGET"

if [ ! -f "$TARGET" ]; then echo "FAIL: target missing (check TC_ROOT)"; exit 2; fi

GOT=$(md5sum "$TARGET" | awk '{print $1}')
if [ "$GOT" = "$NEW_MD5" ]; then
  echo "md5      : MATCH ($GOT)"
else
  echo "md5      : MISMATCH  got=$GOT want=$NEW_MD5"
fi

if python3 -m py_compile "$TARGET" 2>/dev/null; then
  echo "compile  : OK"
else
  echo "compile  : FAIL"
fi

echo "--- D1 markers (expect 1+ hit each) ---"
grep -n "D1_QTY_ANOMALY_RATIO = 1.5"        "$TARGET" || echo "  MISSING: constant"
grep -n "closed_qty = min(qty, q_close)"    "$TARGET" || echo "  MISSING: min attribution"
grep -n "grossly exceeds netted close"      "$TARGET" || echo "  MISSING: flag warning"
grep -n "netted_close_qty"                  "$TARGET" || echo "  MISSING: audit field"

echo "--- markers that must be GONE (old full-qty booking) ---"
if grep -q "fqty = float(agg\[\"total_qty\"\])" "$TARGET"; then
  echo "  STILL PRESENT (unexpected): old fqty booking"
else
  echo "  OK: old fqty booking removed"
fi

echo "=== reminder ==="
echo "Loading D1 needs an engine RESTART (apply does NOT restart)."
echo "Post-restart, on the next real netted close, confirm the"
echo "auto_book_server_side_close audit payload includes 'netted_close_qty'"
echo "and 'qty' == the attributed (capped) share."
