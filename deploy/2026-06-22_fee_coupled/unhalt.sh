#!/usr/bin/env bash
# Re-arm bitunix LIVE entries after the deploy window. Flips the hot kill switch
# bitunix_futures.auto_execute false -> true (block-scoped). HOT: re-arms the
# already-running (post-restart) process with NO restart. Idempotent.
set -euo pipefail
ROOT="${TC_ROOT:-/home/azureuser/trading_corp}"
CFG="$ROOT/config/strategies.yaml"
bxget(){ awk -v k="$1" '/^bitunix_futures:/{f=1} f&&$0~k{print;exit} /^[a-z]/&&!/^bitunix_futures:/{f=0}' "$CFG"; }
[ -f "$CFG" ] || { echo "[unhalt] ABORT: missing $CFG"; exit 2; }
AE=$(bxget "auto_execute"); echo "[unhalt] current: $AE"
echo "$AE" | grep -q 'auto_execute: false' || { echo "[unhalt] already armed (or drifted) — NO change"; exit 0; }
sed -i '/^bitunix_futures:/,/^[a-z]/ { /auto_execute:/ s/auto_execute: false/auto_execute: true/ }' "$CFG"
AE2=$(bxget "auto_execute"); echo "[unhalt] new    : $AE2"
echo "$AE2" | grep -q 'auto_execute: true' || { echo "[unhalt] FAIL: post-change verify"; exit 1; }
python3 -c "import yaml; yaml.safe_load(open('$CFG'))" && echo "[unhalt] yaml OK"
echo "[unhalt] DONE — bitunix LIVE entries RE-ARMED (hot, no restart)."
