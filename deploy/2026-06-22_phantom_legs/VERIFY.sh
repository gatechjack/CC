#!/usr/bin/env bash
# phantom-legs post-apply VERIFY — READ-ONLY. No writes, no restart.
# md5/compile/markers checkable now; the live no-stall behavior needs a restart
# + the next bracketed live trade's close.
ROOT="${TC_ROOT:-/home/azureuser/trading_corp}"
F="$ROOT/trading_corp/agents/paper_trade_replay.py"
NEW="28817062529b23e1d1bf7b5647901469"
echo "=== phantom-legs VERIFY (read-only) ==="
echo "target = $F"
[ -f "$F" ] || { echo "MISSING"; exit 2; }
GOT=$(md5sum "$F" | awk '{print $1}')
if [ "$GOT" = "$NEW" ]; then echo "md5     : MATCH ($GOT)"; else echo "md5     : MISMATCH got=$GOT want=$NEW"; fi
if python3 -m py_compile "$F" 2>/dev/null; then echo "compile : OK"; else echo "compile : FAIL"; fi
echo "--- skip guard present ---"
grep -n "skipped_bracket_managed_live" "$F" | head -3 || echo "  MISSING: skip guard"
echo "--- Issue#1 still present (targeted-hunk preserved it) ---"
if grep -q "suppressed_bracket_managed" "$F"; then echo "  OK: Issue#1 preserved"; else echo "  *** Issue#1 LOST — STOP ***"; fi
echo "=== reminder ==="
echo "Loading needs an engine RESTART. Post-restart, the next bracketed live trade"
echo "must NOT get phantom filled_legs (replay counts 'skipped_bracket_managed_live'),"
echo "so its close auto-books cleanly — no partial_tp_ambiguous stall / halt."
