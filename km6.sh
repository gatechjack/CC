true
R=/home/azureuser/trading_corp/trading_corp
echo "=== prod main.py LF-md5 + linecount ==="
tr -d '\r' < "$R/main.py" | md5sum 2>&1
wc -l < "$R/main.py" 2>&1
echo "=== prod main.py: feed-anomaly push region ==="
grep -n -E "drain_feed_alarms|FEED ANOMALY|synthetic exits SUPPRESSED|Check Apify feed health|feed-anomaly push" "$R/main.py" 2>&1
echo "=== prod strategies.yaml LF-md5 ==="
tr -d '\r' < /home/azureuser/trading_corp/config/strategies.yaml | md5sum 2>&1
echo "=== prod strategies.yaml kalshi_copy_trader block (line-numbered) ==="
grep -n -A34 "^kalshi_copy_trader:" /home/azureuser/trading_corp/config/strategies.yaml 2>&1 | head -40
echo "=== DONE km6 ==="
