#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
#  Fee-RATE correction — bitunix taker_pct 0.0004 -> 0.00019
#  (venue-actual effective rate, Fee Discount/Experience Card; the headline VIP3
#  0.04% taker was ~2.1x too high vs 6 live trades at ~0.019%/leg).
#
#  SCOPE: corrects the model's RATE only. round_trip_cost_pct halves
#  (0.0009 -> ~0.00048), which naturally lowers the TP1 fee floor + the
#  fees_too_high_for_risk gate threshold. It does NOT touch the gate's risk
#  multiplier or any threshold — whether to ACCEPT the resulting looser gate is
#  Step 2's net-edge decision, not this change.
#
#  Surgical: changes ONLY the bitunix_futures fees taker_pct line (block-scoped
#  sed). Drift-gated (current must be 0.0004), backup, re-verify, self-rollback,
#  yaml-parse check. *** NO RESTART *** (FeeConfig loads at startup → operator
#  restarts in the flat window to take effect). Rollback: cp <bak> <cfg> + restart.
# ════════════════════════════════════════════════════════════════════════════
set -euo pipefail
ROOT="${TC_ROOT:-/home/azureuser/trading_corp}"
CFG="$ROOT/config/strategies.yaml"
BAK="$CFG.bak-pre-feerate-2026-06-22"
echo "[fee] config = $CFG"
[ -f "$CFG" ] || { echo "[fee] ABORT: missing $CFG"; exit 2; }

# scoped read of the bitunix fees taker_pct line (block = bitunix_futures: .. next top-level key)
bxline() { awk '/^bitunix_futures:/{f=1} f&&/taker_pct/{print; exit} /^[a-z]/&&!/^bitunix_futures:/{f=0}' "$CFG"; }

CUR=$(bxline)
echo "[fee] current: $CUR"
echo "$CUR" | grep -q 'taker_pct: 0\.0004' || {
  echo "[fee] ABORT: bitunix taker_pct is not 0.0004 (drifted / already corrected) — re-stage vs current prod"; exit 3; }

[ -e "$BAK" ] && { echo "[fee] ABORT: backup already exists ($BAK) — prior apply?"; exit 4; }
cp -p "$CFG" "$BAK"; echo "[fee] backup: $BAK"

sed -i '/^bitunix_futures:/,/^[a-z]/ { /taker_pct/ s/0\.0004/0.00019/; /taker_pct/ s/# 0\.04%/# 0.019% venue-actual effective (Fee Discount Card; was 0.04% headline VIP3 taker)/ }' "$CFG"

NEW=$(bxline)
echo "[fee] new    : $NEW"
echo "$NEW" | grep -q 'taker_pct: 0\.00019' || {
  echo "[fee] FAIL: post-change verify mismatch — ROLLING BACK"; cp -p "$BAK" "$CFG"; exit 5; }

if python3 -c "import yaml; yaml.safe_load(open('$CFG')); print('[fee] yaml parse OK')"; then :; else
  echo "[fee] FAIL: yaml no longer parses — ROLLING BACK"; cp -p "$BAK" "$CFG"; exit 6; fi

# confirm ONLY one line changed vs backup (surgical)
echo "[fee] diff vs backup (expect ONE taker_pct line):"
diff "$BAK" "$CFG" || true
echo "[fee] ─────────────────────────────────────────────────────────────────"
echo "[fee] DONE — taker_pct corrected to 0.00019. NO restart performed."
echo "[fee] NEXT (operator): restart the engine to LOAD the corrected FeeConfig, then VERIFY.sh."
echo "[fee] ROLLBACK: cp \"$BAK\" \"$CFG\"  (then restart)"
