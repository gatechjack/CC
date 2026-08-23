#!/usr/bin/env bash
# CP2 Phase-1 PRE-DEPLOY READ-ONLY inspection: exact contents + ownership/mode of the nested PM code dirs, so the
# root deploy's chown/chmod stays TARGETED (GOTCHA-1 rule 4: never chown broadly). ls only -- no writes, no chown.
# If the nested scripts/ dir interleaves ENGINE scripts with the PM ones, that is a finding to REPORT, not work around.
echo "=== PM P2 CP2 PRE-DEPLOY INSPECT (read-only) ==="
date -u
echo "whoami=$(whoami)"
H="${HOME:-/home/azureuser}"
ROOT="$H/trading_corp"

echo ""
echo "=== [1] parent trading_corp/trading_corp (so the PM subdir MODES are visible) ==="
ls -la "$ROOT/trading_corp" 2>&1

echo ""
echo "=== [2] PM package dir: trading_corp/trading_corp/prediction_markets/ ==="
ls -la "$ROOT/trading_corp/prediction_markets" 2>&1
echo "--- prediction_markets/web/ (should NOT exist yet; created at deploy) ---"
ls -la "$ROOT/trading_corp/prediction_markets/web" 2>&1

echo ""
echo "=== [3] nested scripts dir: trading_corp/trading_corp/scripts/ (PM-only, or engine-interleaved?) ==="
ls -la "$ROOT/trading_corp/scripts" 2>&1

echo ""
echo "=== [4] NON-PM .py in the nested scripts/ (PM = pm_cli.py, pm_web.py) -- any hit => engine-interleaved ==="
found_nonpm=0
for f in "$ROOT/trading_corp/scripts"/*.py; do
  [ -e "$f" ] || continue
  b=$(basename "$f")
  case "$b" in
    pm_cli.py|pm_web.py) : ;;
    *) echo "  NON-PM: $b"; found_nonpm=1 ;;
  esac
done
[ "$found_nonpm" = "0" ] && echo "  (none -- nested scripts/ is PM-only among .py files)"

echo ""
echo "=== [5] context: outer engine scripts dir + PM DB (NOT deploy targets; shown for orientation) ==="
ls -ld "$ROOT/scripts" 2>&1
ls -l "$ROOT/data/prediction_markets.db" 2>&1
echo "=== INSPECT done ==="
