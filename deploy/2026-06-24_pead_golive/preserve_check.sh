#!/usr/bin/env bash
# GATE 1 (post-install, pre-restart): strategies.yaml changed ONLY in the robinhood_pead
# block; everything before it (incl. the Bitunix fee-coupled block) byte-identical to the
# backup; divisions.yaml changed ONLY the robinhood_pead standby line.
set -uo pipefail
TC=/home/azureuser/trading_corp
TAG=pre-golive-2026-06-24
fail(){ echo "PRESERVE-CHECK ABORT: $*" >&2; exit 9; }

S="$TC/config/strategies.yaml"; SB="$S.bak-$TAG"
[ -f "$SB" ] || fail "no backup $SB (run apply_files.sh first)"
ln=$(grep -n '^robinhood_pead:' "$S" | head -1 | cut -d: -f1)
[ -n "$ln" ] || fail "robinhood_pead: not found in installed strategies.yaml"
# robinhood_pead is the last block; compare everything BEFORE it (= all non-PEAD content)
head -n $((ln-1)) "$SB" > /tmp/pc_bak; head -n $((ln-1)) "$S" > /tmp/pc_new
diff -q /tmp/pc_bak /tmp/pc_new >/dev/null || fail "strategies.yaml changed OUTSIDE robinhood_pead (BITUNIX COLLISION)"
echo "  OK: strategies.yaml changed ONLY in the robinhood_pead block"
for v in 'taker_pct: 0.00019' 'tp1_min_profit_multiplier: 3.75' 'maker_pct: 0.00014'; do
  grep -qF "$v" "$S" || fail "bitunix fee-coupled value MISSING post-install: $v"
done
echo "  OK: bitunix fee-coupled intact (taker 0.00019 / tp1 3.75 / maker 0.00014)"
grep -A3 '^robinhood_pead:' "$S" | grep -q 'auto_execute: true' || fail "robinhood_pead auto_execute != true"
echo "  OK: robinhood_pead auto_execute: true"

D="$TC/config/divisions.yaml"; DB="$D.bak-$TAG"
n=$(diff "$DB" "$D" | grep -c '^[<>]')
[ "$n" -eq 2 ] || fail "divisions.yaml diff is not exactly the 1-line standby flip (got $n changed lines)"
grep -A8 'slug: robinhood_pead' "$D" | grep -q 'standby: false' || fail "robinhood_pead standby != false"
echo "  OK: divisions.yaml changed ONLY robinhood_pead standby -> false"
rm -f /tmp/pc_bak /tmp/pc_new
echo "PRESERVE-CHECK PASS (GATE 1)"
