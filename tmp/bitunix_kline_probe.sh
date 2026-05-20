#!/bin/bash
# Three live probes to characterize BitUnix kline behavior.

echo "=== Probe 1: ask for 1m bars from 5/18 16:24 (50h ago) — fetcher's exact query ==="
START_MS=1779121442000
END_MS=1779181440000   # +1000min
curl -s "https://fapi.bitunix.com/api/v1/futures/market/kline?symbol=BTCUSDT&interval=1m&startTime=$START_MS&endTime=$END_MS&limit=1000" > /tmp/probe1.json
python3 << 'PYEOF'
import json
with open("/tmp/probe1.json") as f:
    d = json.load(f)
print(f"code={d.get('code')!r}  msg={d.get('msg')!r}")
data = d.get('data') or []
print(f"rows_returned={len(data)}")
if data:
    print(f"first row: {data[0]}")
    print(f"last row:  {data[-1]}")
PYEOF
echo ""

echo "=== Probe 2: ask for 1m bars from RECENT timestamp (1h ago) ==="
NOW_MS=$(date +%s%3N)
START_MS=$((NOW_MS - 3600000))
END_MS=$NOW_MS
curl -s "https://fapi.bitunix.com/api/v1/futures/market/kline?symbol=BTCUSDT&interval=1m&startTime=$START_MS&endTime=$END_MS&limit=1000" > /tmp/probe2.json
python3 << 'PYEOF'
import json
with open("/tmp/probe2.json") as f:
    d = json.load(f)
print(f"code={d.get('code')!r}  msg={d.get('msg')!r}")
data = d.get('data') or []
print(f"rows_returned={len(data)}")
if data:
    print(f"first row: {data[0]}")
    print(f"last row:  {data[-1]}")
PYEOF
echo ""

echo "=== Probe 3: only limit, no startTime/endTime ==="
curl -s "https://fapi.bitunix.com/api/v1/futures/market/kline?symbol=BTCUSDT&interval=1m&limit=3" > /tmp/probe3.json
python3 << 'PYEOF'
import json
with open("/tmp/probe3.json") as f:
    d = json.load(f)
print(f"code={d.get('code')!r}  msg={d.get('msg')!r}")
data = d.get('data') or []
print(f"rows_returned={len(data)}")
for r in data:
    print(r)
PYEOF
echo ""

echo "=== Probe 4: ask 5/18 16:24 with NO endTime ==="
curl -s "https://fapi.bitunix.com/api/v1/futures/market/kline?symbol=BTCUSDT&interval=1m&startTime=1779121442000&limit=10" > /tmp/probe4.json
python3 << 'PYEOF'
import json
with open("/tmp/probe4.json") as f:
    d = json.load(f)
print(f"code={d.get('code')!r}  msg={d.get('msg')!r}")
data = d.get('data') or []
print(f"rows_returned={len(data)}")
for r in data[:3]:
    print(r)
PYEOF
echo ""

echo "=== Probe 5: ask startTime=5/18 16:24 with TYPE=3m (longer-retention timeframe) ==="
curl -s "https://fapi.bitunix.com/api/v1/futures/market/kline?symbol=BTCUSDT&interval=3m&startTime=1779121442000&endTime=1779181440000&limit=10" > /tmp/probe5.json
python3 << 'PYEOF'
import json
with open("/tmp/probe5.json") as f:
    d = json.load(f)
print(f"code={d.get('code')!r}  msg={d.get('msg')!r}")
data = d.get('data') or []
print(f"rows_returned={len(data)}")
for r in data[:3]:
    print(r)
PYEOF
echo "=== DONE ==="
