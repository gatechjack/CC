#!/usr/bin/env bash
# Gate-A + backup + compile — NO stop, NO mv. Verifies the deploy is EXACTLY this fix
# on top of the current prod baseline, backs up the live files, and syntax/YAML-checks
# the staged .new set on the prod venv. Any FAIL => abort upstream (engine untouched).
set +e
APPROOT=$(systemctl show -p WorkingDirectory --value trading-corp 2>/dev/null); [ -d "$APPROOT" ] || APPROOT=/home/azureuser/trading_corp
PY="$APPROOT/.venv/bin/python"
BAK=".bak_pmcc_pricefix_20260804"
cd "$APPROOT" || { echo "APPROOT_FAIL"; exit 3; }
echo "APPROOT=$APPROOT  PY=$PY  python=$($PY -V 2>&1)"

# file : baseline(current prod should ==) : target(.new should ==)
MANIFEST="config/strategies.yaml|274b7e348eb2|ce2f1c0ee5fc
trading_corp/agents/divisions/pmcc_robinhood.py|6b928badbcf0|0d199b237c05
trading_corp/web/pmcc_pricing.py|7ee14a4367b6|af9a674e79aa
trading_corp/web/routes.py|96becb83b19a|c15e84c74521"

FAIL=0
echo "---GATE-A (live==baseline, .new==target)---"
while IFS='|' read -r f base target; do
  live=$(tr -d '\r' < "$f" 2>/dev/null | md5sum | cut -c1-12)
  new=$(tr -d '\r' < "$f.new" 2>/dev/null | md5sum | cut -c1-12)
  ok_live=$([ "$live" = "$base" ] && echo LIVE_OK || echo LIVE_DRIFT)
  ok_new=$([ "$new" = "$target" ] && echo NEW_OK || echo NEW_BAD)
  [ "$ok_live" = LIVE_OK ] || FAIL=1
  [ "$ok_new" = NEW_OK ] || FAIL=1
  printf "%-52s live=%s(%s) new=%s(%s)\n" "$f" "$live" "$ok_live" "$new" "$ok_new"
done <<< "$MANIFEST"

echo "---BACKUP live -> *$BAK (azureuser-owned, -p)---"
for f in config/strategies.yaml trading_corp/agents/divisions/pmcc_robinhood.py trading_corp/web/pmcc_pricing.py trading_corp/web/routes.py; do
  cp -p "$f" "$f$BAK" && echo "backed up $f$BAK ($(stat -c '%U:%G %a' "$f$BAK"))" || { echo "BACKUP_FAIL $f"; FAIL=1; }
done

echo "---py_compile .new (syntax) + YAML validate---"
$PY -m py_compile trading_corp/agents/divisions/pmcc_robinhood.py.new && echo "compile OK pmcc_robinhood" || { echo "COMPILE_FAIL pmcc_robinhood"; FAIL=1; }
$PY -m py_compile trading_corp/web/pmcc_pricing.py.new && echo "compile OK pmcc_pricing" || { echo "COMPILE_FAIL pmcc_pricing"; FAIL=1; }
$PY -m py_compile trading_corp/web/routes.py.new && echo "compile OK routes" || { echo "COMPILE_FAIL routes"; FAIL=1; }
$PY -c "import yaml; yaml.safe_load(open('config/strategies.yaml.new')); print('YAML OK strategies (robinhood_pmcc auto_execute=%s)' % yaml.safe_load(open('config/strategies.yaml.new'))['robinhood_pmcc']['auto_execute'])" || { echo "YAML_FAIL strategies"; FAIL=1; }

echo "---RESULT: $([ $FAIL -eq 0 ] && echo GATE_A_PASS || echo GATE_A_FAIL)---"
