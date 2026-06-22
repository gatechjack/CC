#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
#  Fee COUPLED correction (Decision A) — supersedes the rate-only 2026-06-22 stage.
#  HONESTY fix (truthful model, SAME gating behavior), NOT a profitability change.
#
#   1) bitunix fees.taker_pct          0.0004 -> 0.00019   (venue-actual effective)
#   2) bitunix trade_plan.tp1_min_profit_multiplier  2.0 -> 3.75  (x1.875)
#
#  WHY COUPLED: the rate correction alone halves round_trip_cost_pct
#  (0.0009 -> 0.00048), which would lower the TP1 fee floor and loosen the
#  fees_too_high_for_risk gate to admit 183 NET-NEGATIVE trades (Step 2, −0.368R,
#  6/6 windows). fee_floor = mult x round_trip. Scaling mult x1.875 holds
#  fee_floor IDENTICAL (2.0x0.0009 = 3.75x0.00048 = 0.0018) -> the gate skips the
#  SAME cohort and TP1 lands in the SAME place, but on the TRUE rate. Same
#  behavior, true inputs. Flipped cohort -> 0 by construction; not more conservative.
#
#  Surgical: ONLY the 2 bitunix lines (block-scoped sed). Drift-gated (taker 0.0004
#  AND mult 2.0), backup, re-verify both, yaml-parse, diff-bounded (2 lines),
#  self-rollback. *** NO RESTART *** (FeeConfig+StrategyConfig load at startup).
#  Rollback: cp <bak> <cfg> + restart.
# ════════════════════════════════════════════════════════════════════════════
set -euo pipefail
ROOT="${TC_ROOT:-/home/azureuser/trading_corp}"
CFG="$ROOT/config/strategies.yaml"
BAK="$CFG.bak-pre-feecoupled-2026-06-22"
echo "[fee] config = $CFG"
[ -f "$CFG" ] || { echo "[fee] ABORT: missing $CFG"; exit 2; }

bxget() { awk -v k="$1" '/^bitunix_futures:/{f=1} f&&$0 ~ k{print; exit} /^[a-z]/&&!/^bitunix_futures:/{f=0}' "$CFG"; }

TK=$(bxget "taker_pct"); MU=$(bxget "tp1_min_profit_multiplier")
echo "[fee] current taker_pct : $TK"
echo "[fee] current tp1_mult  : $MU"
echo "$TK" | grep -q 'taker_pct: 0\.0004' || { echo "[fee] ABORT: taker_pct != 0.0004 (drifted)"; exit 3; }
echo "$MU" | grep -q 'tp1_min_profit_multiplier: 2\.0' || { echo "[fee] ABORT: tp1_min_profit_multiplier != 2.0 (drifted)"; exit 3; }

[ -e "$BAK" ] && { echo "[fee] ABORT: backup exists ($BAK)"; exit 4; }
cp -p "$CFG" "$BAK"; echo "[fee] backup: $BAK"

sed -i '/^bitunix_futures:/,/^[a-z]/ {
  /taker_pct/ s/0\.0004/0.00019/
  /taker_pct/ s/# 0\.04%/# 0.019% venue-actual effective (Fee Discount Card; was 0.04% headline)/
  /tp1_min_profit_multiplier/ s/2\.0/3.75/
  /tp1_min_profit_multiplier/ s/$/  # x1.875: preserves fee_floor (gate behavior) at the corrected taker; was 2.0/
}' "$CFG"

TK2=$(bxget "taker_pct"); MU2=$(bxget "tp1_min_profit_multiplier")
echo "[fee] new taker_pct : $TK2"
echo "[fee] new tp1_mult  : $MU2"
{ echo "$TK2" | grep -q 'taker_pct: 0\.00019' && echo "$MU2" | grep -q 'tp1_min_profit_multiplier: 3\.75'; } || {
  echo "[fee] FAIL: post-change verify mismatch — ROLLING BACK"; cp -p "$BAK" "$CFG"; exit 5; }

python3 -c "import yaml; yaml.safe_load(open('$CFG'))" || { echo "[fee] FAIL: yaml parse — ROLLING BACK"; cp -p "$BAK" "$CFG"; exit 6; }

N=$(diff "$BAK" "$CFG" | grep -c '^>') || true
echo "[fee] changed lines: $N (expect 2 — taker_pct + tp1_min_profit_multiplier)"
[ "$N" = "2" ] || { echo "[fee] FAIL: diff not bounded to 2 lines — ROLLING BACK"; cp -p "$BAK" "$CFG"; exit 7; }
echo "[fee] diff vs backup:"; diff "$BAK" "$CFG" || true
echo "[fee] ─────────────────────────────────────────────────────────────────"
echo "[fee] DONE — coupled correction applied. NO restart performed."
echo "[fee] NEXT (operator): restart to LOAD, then VERIFY.sh."
echo "[fee] ROLLBACK: cp \"$BAK\" \"$CFG\"  (then restart)"
