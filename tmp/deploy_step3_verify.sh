#!/bin/bash
# Step 3: reality-verify the deploy.
#  3a. Exercise the deployed _bitunix_kline_fetcher with a wide window
#      and confirm it returns the FULL window (not 200) — i.e. the
#      pagination fix is live in this process.
#  3b. Run the deployed reconciler against the prod DB; expect the
#      same 1-mismatch / 1-match result as the local run, since the
#      classifier code is unchanged and the corrected extra_json fields
#      don't affect the reconciler (which starts from a fresh state).

set -e
BASE=/home/azureuser/trading_corp

echo "=== 3a: exercise deployed fetcher with trade-#1's actual window ==="
sudo -u azureuser bash -c "cd $BASE && /home/azureuser/trading_corp/venv/bin/python <<'PYEOF'
import sys, asyncio, json
sys.path.insert(0, '/home/azureuser/trading_corp')
from trading_corp.agents.paper_trade_replay import _bitunix_kline_fetcher

# Trade #1 entry: 2026-05-18T16:24:02Z (1779121442000 ms)
# Max-hold: 86400 s = 1440 1-min bars
since_ms = 1779121442000
limit = 1440

async def run():
    bars = await _bitunix_kline_fetcher(
        symbol='BTCUSDT.P', timeframe='1m',
        since_ms=since_ms, limit=limit,
    )
    print(f'requested: {limit} bars')
    print(f'returned : {len(bars)} bars')
    if bars:
        print(f'first ts  : {bars[0][0]}  (= {since_ms} ?)')
        print(f'last ts   : {bars[-1][0]}')
        print(f'span min  : {(bars[-1][0] - bars[0][0]) / 60_000:.1f}')
    # Pass criterion: returned > 200 (the legacy single-page cap).
    # Should be ~1440 (full requested window); some bars may be absent
    # if BitUnix doesn't have them, but at minimum we expect many more
    # than 200.
    if len(bars) <= 200:
        print('FAIL: fetcher still returns <=200 bars; pagination fix is NOT live in this process')
        sys.exit(2)
    else:
        print(f'OK: fetcher returned {len(bars)} bars > 200 ; pagination fix is LIVE')
asyncio.run(run())
PYEOF
"

echo ""
echo "=== 3b: run deployed reconciler against prod DB ==="
sudo -u azureuser bash -c "cd $BASE && PYTHONPATH=/home/azureuser/trading_corp /home/azureuser/trading_corp/venv/bin/python scripts/audit_reality_reconciler.py --db sqlite:////home/azureuser/trading_corp/data/trading_corp.db"
RECON_RC=$?
echo ""
echo "Reconciler exit code: $RECON_RC"
echo "(exit 1 = at least one mismatch found, which is EXPECTED on the historical data — trade #1 stays mismatched until original result/R columns are also corrected OR the reconciler is changed to compare against extra_json.corrected_*)"
echo ""

echo "=== DONE ==="
