#!/bin/bash
# Inner deploy script for kalshi_sports_arb_observer (Phase 0, MLB).
# Runs ON PROD inside /tmp/sports_arb_observer_files/ after the outer
# script extracts this bundle.
#
# Idempotent: rerunning is safe (won't double-insert, will overwrite
# .py files which is fine since they're tracked in git).
set -e

BASE=/home/azureuser/trading_corp
TS=$(date -u +%Y-%m-%d-%H%M%S)
BACKUP=/tmp/sports_arb_observer_backup_${TS}
STAGING=$(dirname "$0")

mkdir -p "$BACKUP"
echo ">>> Backups -> $BACKUP"

# 1. Back up files about to be modified
cp -v "$BASE/trading_corp/main.py" "$BACKUP/main.py.bak"
cp -v "$BASE/config/strategies.yaml" "$BACKUP/strategies.yaml.bak"
[ -f "$BASE/trading_corp/data/odds_api_client.py" ] && cp -v "$BASE/trading_corp/data/odds_api_client.py" "$BACKUP/odds_api_client.py.bak" || true

# 2. Verify expected md5s on the bundled .py files (catches transport corruption)
echo ">>> Verifying bundled file md5s"
declare -A EXPECTED
EXPECTED["trading_corp/agents/strategies/_sports_math.py"]="23f8cc2e92037893d25080b77bf9cef2"
EXPECTED["trading_corp/agents/strategies/kalshi_sports_arb_observer.py"]="73e7ca73e641102783ea37e11b5f3547"
EXPECTED["trading_corp/data/odds_api_client.py"]="63da9c0c929e9c6a49e983c7f09af2c6"
for f in "${!EXPECTED[@]}"; do
  ACTUAL=$(md5sum "$STAGING/$f" | awk '{print $1}')
  if [ "$ACTUAL" != "${EXPECTED[$f]}" ]; then
    echo "MD5 MISMATCH on $f: expected ${EXPECTED[$f]}, got $ACTUAL"
    exit 1
  fi
  echo "  $f OK ($ACTUAL)"
done

# 3. Copy the 3 .py files into prod
echo ">>> Installing files"
cp -v "$STAGING/trading_corp/agents/strategies/_sports_math.py" "$BASE/trading_corp/agents/strategies/"
cp -v "$STAGING/trading_corp/agents/strategies/kalshi_sports_arb_observer.py" "$BASE/trading_corp/agents/strategies/"
cp -v "$STAGING/trading_corp/data/odds_api_client.py" "$BASE/trading_corp/data/"

# 4. Patch main.py (idempotent: only insert if not already present)
echo ">>> Patching main.py (idempotent)"
/home/azureuser/trading_corp/venv/bin/python3 - <<PY
import sys
main_py = "$BASE/trading_corp/main.py"
src = open(main_py).read()

# Insert 1: agent instantiation block, BEFORE Copy Trader scanner block
anchor1 = '        # --- Kalshi Copy Trader scanner (Phase K3; default off) ---'
if 'KalshiSportsArbObserverAgent' in src:
    print("  agent instantiation already present; skipping insert 1")
else:
    block1 = open("$STAGING/main_block_1.txt").read()
    if anchor1 not in src:
        print("ANCHOR 1 NOT FOUND in main.py — aborting"); sys.exit(1)
    src = src.replace(anchor1, block1 + anchor1, 1)
    print("  agent instantiation inserted")

# Insert 2: scheduler loop function, BEFORE if __name__ == "__main__":
if '_scheduled_kalshi_sports_arb_observer_loop' in src.replace(
    'kalshi_sports_arb_observer_task = asyncio.create_task(\n            _scheduled_kalshi_sports_arb_observer_loop',
    'XXX_marker_for_insert_1_only',
):
    print("  scheduler loop function already present; skipping insert 2")
else:
    anchor2 = 'if __name__ == "__main__":'
    block2 = open("$STAGING/main_block_2.txt").read()
    if anchor2 not in src:
        print("ANCHOR 2 NOT FOUND in main.py — aborting"); sys.exit(1)
    src = src.replace(anchor2, block2 + anchor2, 1)
    print("  scheduler loop function inserted")

open(main_py, 'w').write(src)
print("main.py written")
PY

# 5. Patch strategies.yaml (idempotent)
echo ">>> Patching strategies.yaml (idempotent)"
/home/azureuser/trading_corp/venv/bin/python3 - <<PY
import sys
yaml_path = "$BASE/config/strategies.yaml"
src = open(yaml_path, encoding='utf-8').read()
if 'kalshi_sports_arb_observer:' in src:
    print("  kalshi_sports_arb_observer block already present; skipping")
else:
    anchor = 'kalshi_copy_trader:'
    block = open("$STAGING/yaml_block.txt", encoding='utf-8').read()
    if anchor not in src:
        print("ANCHOR NOT FOUND in strategies.yaml — aborting"); sys.exit(1)
    src = src.replace(anchor, block + anchor, 1)
    open(yaml_path, 'w', encoding='utf-8').write(src)
    print("  block inserted")
PY

# 6. Smoke-test imports BEFORE restart (catches syntax errors / missing modules early)
echo ">>> Smoke-testing imports"
cd "$BASE" && /home/azureuser/trading_corp/venv/bin/python3 -c "
from trading_corp.agents.strategies._sports_math import kalshi_fee, LegFill
from trading_corp.agents.strategies.kalshi_sports_arb_observer import KalshiSportsArbObserverAgent, _PHASE0_LEAGUE_CLASSIFIERS
from trading_corp.data.odds_api_client import OddsAPIClient, GameLine, BookPrice
print('observer imports OK; dispatch:', list(_PHASE0_LEAGUE_CLASSIFIERS.keys()))
# Confirm scheduler loop function is on main.py module
import importlib, trading_corp.main as tm
assert hasattr(tm, '_scheduled_kalshi_sports_arb_observer_loop'), 'scheduler loop fn missing from main.py'
print('main.py has _scheduled_kalshi_sports_arb_observer_loop')
"

# 7. Restart the service
echo ">>> Restarting trading-corp.service"
sudo systemctl restart trading-corp.service
sleep 5
echo ">>> Service status:"
systemctl is-active trading-corp.service
echo ">>> Recent service log (last 25 lines):"
journalctl -u trading-corp.service -n 25 --no-pager | tail -25

echo ""
echo ">>> Deploy complete."
echo "Watch for:"
echo "  - 'Kalshi Sports Arb Observer online' log line"
echo "  - First 'kalshi_sports_arb_scan' audit row ~1h after restart"
echo "  - First 'kalshi_sports_arb_observation' rows shortly after"
echo "Backups at: $BACKUP"
