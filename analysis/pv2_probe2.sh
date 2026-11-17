echo "=== PV2 PROBE 2: cursor-walk + offset acceptance (RO public GETs, ~6 calls, 2s apart) ==="
date -u +%FT%TZ
UA="tc-pm-v2-migration-scoping-ro/1.0"
B="https://data-api.polymarket.com/v2"
W1=0x21f333307ac6d2e65ac82f014b3dd2b64cbc35e3

echo ""; echo "--- [1] positions page1 limit=2 (capture next_cursor) ---"
R1=$(curl -s -m 15 -A "$UA" "$B/positions?user=$W1&limit=2")
echo "$R1" | grep -o '"condition_id":"[^"]*"' | head -2
echo "$R1" | grep -o '"pagination":.*' | head -c 400
C1=$(echo "$R1" | grep -o '"next_cursor":"[^"]*"' | head -1 | sed 's/.*:"//; s/"$//')
echo ""; echo "next_cursor_len=${#C1}"
sleep 2

echo ""; echo "--- [2] positions page2 via cursor param (verify distinct rows) ---"
if [ -n "$C1" ]; then
  R2=$(curl -s -m 15 -A "$UA" -G --data-urlencode "user=$W1" --data-urlencode "limit=2" --data-urlencode "cursor=$C1" "$B/positions")
  echo "$R2" | grep -o '"condition_id":"[^"]*"' | head -2
  echo "$R2" | grep -o '"pagination":.*' | head -c 400
else
  echo "NO CURSOR on page1 (short book?)"
fi
sleep 2

echo ""; echo "--- [3] activity offset=0 limit=3 (baseline tx hashes) ---"
curl -s -m 15 -A "$UA" "$B/activity?user=$W1&limit=3&offset=0" -w '\nHTTP:%{http_code}\n' | grep -oE '"transaction_hash":"0x.{8}|HTTP:[0-9]*' | head -5
sleep 2
echo ""; echo "--- [4] activity offset=3 limit=3 (OFFSET ACCEPTANCE: 400 or distinct rows?) ---"
curl -s -m 15 -A "$UA" "$B/activity?user=$W1&limit=3&offset=3" -w '\nHTTP:%{http_code}\n' | grep -oE '"transaction_hash":"0x.{8}|HTTP:[0-9]*|"error":"[^"]*"' | head -6
sleep 2

echo ""; echo "--- [5] closed positions offset=3 limit=3 ---"
curl -s -m 15 -A "$UA" "$B/positions?user=$W1&status=CLOSED&limit=3&offset=3" -w '\nHTTP:%{http_code}\n' | grep -oE '"condition_id":"0x.{8}|HTTP:[0-9]*|"error":"[^"]*"' | head -6
sleep 2

echo ""; echo "--- [6] leaderboard offset=3 limit=3 ---"
curl -s -m 15 -A "$UA" "$B/leaderboard?limit=3&offset=3" -w '\nHTTP:%{http_code}\n' | grep -oE '"user_id":"0x.{8}|HTTP:[0-9]*|"error":"[^"]*"' | head -6

echo ""; echo "=== END PROBE 2 ==="
