echo "=== PM A3: LIVE Polymarket v2 field probe (READ-ONLY public GET, throttled) ==="
date -u +%FT%TZ
ROOT=/home/azureuser/trading_corp
RO_PM="file:$ROOT/data/prediction_markets.db?mode=ro"
UA="tc-pm-migration-scoping-ro/1.0"
# budget: 3 wallets x {open, closed} + 1 leaderboard + 1 v1-sanity = 8 calls, 2s apart. Stop on 429.
W1=0x21f333307ac6d2e65ac82f014b3dd2b64cbc35e3
W2=0x746295855be6f49fe2da2accd69ea34b3cf23ac3
W3=0xe8c4d68aff65b38cac46987b9b65e01eb47d395d

echo ""; echo "--- [0] v1 SANITY: confirm v1 /positions is really 410 ---"
curl -s -m 15 -A "$UA" -w '\nHTTP_STATUS:%{http_code}\n' "https://data-api.polymarket.com/positions?user=$W1&limit=2" | head -c 400
sleep 2

echo ""; echo "--- [1] v2 /v2/positions OPEN (W1) ---"
curl -s -m 15 -A "$UA" -w '\nHTTP_STATUS:%{http_code}\n' "https://data-api.polymarket.com/v2/positions?user=$W1&limit=3" | head -c 2200
sleep 2
echo ""; echo "--- [2] v2 /v2/positions CLOSED (W1) ---"
curl -s -m 15 -A "$UA" -w '\nHTTP_STATUS:%{http_code}\n' "https://data-api.polymarket.com/v2/positions?user=$W1&status=CLOSED&limit=2" | head -c 1800
sleep 2
echo ""; echo "--- [3] v2 /v2/positions OPEN (W2 = ITF whale 0x7462) ---"
curl -s -m 15 -A "$UA" -w '\nHTTP_STATUS:%{http_code}\n' "https://data-api.polymarket.com/v2/positions?user=$W2&limit=3" | head -c 2200
sleep 2
echo ""; echo "--- [4] v2 /v2/positions OPEN (W3) ---"
curl -s -m 15 -A "$UA" -w '\nHTTP_STATUS:%{http_code}\n' "https://data-api.polymarket.com/v2/positions?user=$W3&limit=2" | head -c 1600
sleep 2
echo ""; echo "--- [5] v2 /v2/activity (W1) ---"
curl -s -m 15 -A "$UA" -w '\nHTTP_STATUS:%{http_code}\n' "https://data-api.polymarket.com/v2/activity?user=$W1&limit=2" | head -c 1600
sleep 2
echo ""; echo "--- [6] v2 /v2/leaderboard (discovery) ---"
curl -s -m 15 -A "$UA" -w '\nHTTP_STATUS:%{http_code}\n' "https://data-api.polymarket.com/v2/leaderboard?limit=3" | head -c 1400

echo ""; echo "--- [7] stored v1-era pm_open_position rows for W1 (to diff field semantics) ---"
sqlite3 -cmd ".timeout 8000" -cmd ".mode line" "$RO_PM" "SELECT condition_id, outcome, outcome_index, size, avg_price, initial_value, current_value, cash_pnl, datetime(refreshed_ts,'unixepoch') refreshed FROM pm_open_position WHERE wallet='$W1' ORDER BY refreshed_ts DESC LIMIT 2;" 2>&1

echo ""; echo "=== END A3 PROBE ==="
