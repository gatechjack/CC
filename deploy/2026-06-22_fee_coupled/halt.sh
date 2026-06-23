#!/usr/bin/env bash
# Halt bitunix LIVE entries for the deploy window. Flips the hot kill switch
# bitunix_futures.auto_execute true -> false (block-scoped). HOT: the observer
# fresh-reads it per placement, so this blocks the NEXT entry with NO restart,
# and it persists across the upcoming restart (new process loads false).
# Un-halt with unhalt.sh (also hot). Idempotent; aborts cleanly if already halted.
set -euo pipefail
ROOT="${TC_ROOT:-/home/azureuser/trading_corp}"
CFG="$ROOT/config/strategies.yaml"
bxget(){ awk -v k="$1" '/^bitunix_futures:/{f=1} f&&$0~k{print;exit} /^[a-z]/&&!/^bitunix_futures:/{f=0}' "$CFG"; }
[ -f "$CFG" ] || { echo "[halt] ABORT: missing $CFG"; exit 2; }
AE=$(bxget "auto_execute"); echo "[halt] current: $AE"
echo "$AE" | grep -q 'auto_execute: true' || { echo "[halt] already halted (or drifted) — NO change"; exit 0; }
sed -i '/^bitunix_futures:/,/^[a-z]/ { /auto_execute:/ s/auto_execute: true/auto_execute: false/ }' "$CFG"
AE2=$(bxget "auto_execute"); echo "[halt] new    : $AE2"
echo "$AE2" | grep -q 'auto_execute: false' || { echo "[halt] FAIL: post-change verify"; exit 1; }
python3 -c "import yaml; yaml.safe_load(open('$CFG'))" && echo "[halt] yaml OK"
echo "[halt] DONE — bitunix LIVE entries HALTED (hot, no restart). Re-arm: bash ~/unhalt.sh"
