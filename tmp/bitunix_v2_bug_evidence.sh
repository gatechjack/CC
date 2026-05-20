#!/bin/bash
DB=/home/azureuser/trading_corp/data/trading_corp.db

run() {
  echo "--- $1 ---"
  sqlite3 -readonly "$DB" "$2" 2>&1 || true
  echo ""
}

# Crucial: what's max_hold_seconds on the 2 trades?
run "max_hold_seconds + symbol on the 2 trades" \
"SELECT order_id, ts, symbol, max_hold_seconds, qty, expected_loss, expected_gain, tp_r_multiple,
        entry_reference_price, stop_price, tp_price, bars_to_resolution
 FROM paper_trade_record
 WHERE division='bitunix_futures' AND ts LIKE '2026-05-18T1%'
 ORDER BY ts;"

# Live test the BitUnix kline endpoint with the trade-#1 startTime
echo "=== BitUnix kline LIVE test: ask for 1m bars from 5/18 16:24 ==="
START_MS=$(date -d '2026-05-18 16:24:02 UTC' +%s%3N)
END_MS=$(date -d '2026-05-19 09:04:00 UTC' +%s%3N)
echo "startTime=$START_MS endTime=$END_MS"
curl -s "https://fapi.bitunix.com/api/v1/futures/market/kline?symbol=BTCUSDT&interval=1m&startTime=$START_MS&endTime=$END_MS&limit=1000" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'code={d.get(chr(34)+chr(99)+chr(111)+chr(100)+chr(101)+chr(34))} msg={d.get(chr(34)+chr(109)+chr(115)+chr(103)+chr(34))!r}'); rows=d.get(chr(34)+chr(100)+chr(97)+chr(116)+chr(97)+chr(34)) or []; print(f'rows_returned={len(rows)}'); print(f'first 2: {rows[:2]}'); print(f'last 2: {rows[-2:] if rows else []}')"
echo ""

# Live test: no startTime (default — most recent N)
echo "=== BitUnix kline LIVE test: no startTime, default 100 bars ==="
curl -s "https://fapi.bitunix.com/api/v1/futures/market/kline?symbol=BTCUSDT&interval=1m&limit=10" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); rows=d.get('data') or []; print(f'rows={len(rows)}'); [print(r) for r in rows[:5]]"
echo ""

# Check the trade #1 entry payload — could max_hold_seconds=60 trigger bars_needed=1?
run "Trade #1 full row to inspect bars_to_resolution path" \
"SELECT order_id, ts, max_hold_seconds, expected_loss, expected_gain,
        bars_to_resolution, result_ts, result_price, actual_r_multiple
 FROM paper_trade_record WHERE order_id='35aa49c9-bb62-4084-865f-5d839515cd81';"

# Look for how paper_trade_replay loop is scheduled
echo "=== Scheduling: grep main.py for paper_trade_replay ==="
grep -n "paper_trade_replay\|replay_pending\|_replay_tick" /home/azureuser/trading_corp/trading_corp/main.py 2>&1 | head -30
echo ""

echo "=== last 5 lines of paper_trade_replay if there's a loop wrapper ==="
grep -n "run_paper_trade_replay\|run_replay_loop\|async def run\|asyncio.sleep" /home/azureuser/trading_corp/trading_corp/agents/paper_trade_replay.py 2>&1 | head -20
echo ""

# Persistence-path check: _persist_extra_json + _extra_json_delta
echo "=== _persist_extra_json + _extra_json_delta source ==="
grep -n "_persist_extra_json\|_extra_json_delta" /home/azureuser/trading_corp/trading_corp/agents/paper_trade_replay.py | head -10
echo ""

# 1m bar availability — does bitunix_bar_history have 1m, or only 3m+?
run "bar_history TIMEFRAMES distinct" "SELECT DISTINCT timeframe FROM bitunix_bar_history;"

echo "=== DONE ==="
