#!/usr/bin/env bash
set -u
B=/home/azureuser/trading_corp
echo "=== diff prod polymarket_data_api_client.py vs git HEAD~1 ==="
diff <(sudo -u azureuser git -C $B cat-file -p HEAD~1:trading_corp/data/polymarket_data_api_client.py 2>&1 || echo "(no git on prod)") \
     $B/trading_corp/data/polymarket_data_api_client.py 2>&1 | head -200
echo
echo "=== first 30 + last 30 lines of prod polymarket_data_api_client.py ==="
head -30 $B/trading_corp/data/polymarket_data_api_client.py
echo "..."
tail -30 $B/trading_corp/data/polymarket_data_api_client.py
echo
echo "=== grep for any non-30f8abe markers in client (sniff for parallel-session edits) ==="
grep -nE "_get_json|PolymarketRateLimit|cloudflare|cf-ray|retry|backoff|CRLF" $B/trading_corp/data/polymarket_data_api_client.py | head -20
echo
echo "=== md5 + line count ==="
md5sum $B/trading_corp/data/polymarket_data_api_client.py
wc -l $B/trading_corp/data/polymarket_data_api_client.py
md5sum $B/trading_corp/scripts/seed_polymarket_watchlist_deep.py
wc -l $B/trading_corp/scripts/seed_polymarket_watchlist_deep.py
echo
echo "=== first 30 lines + last 30 lines of prod seed_polymarket_watchlist_deep.py ==="
head -30 $B/trading_corp/scripts/seed_polymarket_watchlist_deep.py
echo "..."
tail -30 $B/trading_corp/scripts/seed_polymarket_watchlist_deep.py
echo
echo "=== DONE ==="
