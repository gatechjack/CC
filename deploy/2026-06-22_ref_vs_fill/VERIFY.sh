#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
#  ref-vs-fill post-apply VERIFY — READ-ONLY. No writes, no restart.
#  Run AFTER apply_refvfill.sh. md5/compile/markers checkable immediately;
#  the live per-trade fill-vs-ref check needs a restart + the next live close.
# ════════════════════════════════════════════════════════════════════════════
ROOT="${TC_ROOT:-/home/azureuser/trading_corp}"
OBS="$ROOT/trading_corp/agents/divisions/bitunix_futures_observer.py"
REC="$ROOT/trading_corp/agents/divisions/bitunix_position_reconciler.py"
OBS_NEW="2647fccc630c8acacbe0d5a32f05b1c8"
REC_NEW="a3e9d50da2527664a2016e7205cac9f8"

echo "=== ref-vs-fill VERIFY (read-only) ==="
for pair in "observer:$OBS:$OBS_NEW" "reconciler:$REC:$REC_NEW"; do
  name="${pair%%:*}"; rest="${pair#*:}"; path="${rest%%:*}"; want="${rest##*:}"
  if [ ! -f "$path" ]; then echo "$name: MISSING ($path)"; continue; fi
  got=$(md5sum "$path" | awk '{print $1}')
  if [ "$got" = "$want" ]; then echo "$name md5 : MATCH ($got)"
  else echo "$name md5 : MISMATCH got=$got want=$want"; fi
done

if python3 -m py_compile "$OBS" "$REC" 2>/dev/null; then echo "compile : OK (both)"; else echo "compile : FAIL"; fi

echo "--- ref-vs-fill markers ---"
grep -n "actual_entry_fill_price" "$OBS" | head -2 || echo "  MISSING: observer capture"
grep -n "def _resolve_entry_price" "$REC" || echo "  MISSING: reconciler helper"
grep -c "_resolve_entry_price(extra, r\[\"entry_reference_price\"\])" "$REC" \
  | awk '{print "  consume sites using helper:",$1,"(want 2)"}'

echo "--- D4 guard MUST still be present in observer (targeted-hunk preserved it) ---"
if grep -q "_concurrent_position_guard_verdict" "$OBS"; then echo "  OK: D4 guard preserved"; else echo "  *** D4 GUARD LOST — STOP ***"; fi

echo "=== reminder ==="
echo "Loading ref-vs-fill needs an engine RESTART (apply does NOT restart)."
echo "Post-restart, on the next LIVE entry the record gets extra.actual_entry_fill_price"
echo "(= the real fill, != entry_reference_price); the next live close books PnL from it."
