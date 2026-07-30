true
R=/home/azureuser/trading_corp/trading_corp
echo "=== file identity: raw md5 | LF-normalized md5 | linecount ==="
for f in "$R/agents/strategies/kalshi_copy_trader.py" "$R/data/kalshi_apify_client.py" "$R/brokers/kalshi.py"; do
  echo "--- $f"
  md5sum "$f" 2>&1
  tr -d '\r' < "$f" | md5sum 2>&1
  wc -l < "$f" 2>&1
done
echo "=== strategies.yaml LF-md5 ==="
tr -d '\r' < /home/azureuser/trading_corp/config/strategies.yaml | md5sum 2>&1
echo "=== kalshi_copy_trader.py anchors (loci on prod) ==="
grep -n -E "def run_scan_cycle|def _is_mass_disappearance|def _queue_feed_anomaly|if self\._is_mass_disappearance|_save_whale_snapshot_raw|self\._consecutive_fetch_failures|def _feed_cfg|class TradeTapeFetcher|def drain_feed_alarms|continue   # prev snapshot" "$R/agents/strategies/kalshi_copy_trader.py" 2>&1
echo "=== brokers/kalshi.py: get_market / status / class ==="
grep -n -E "class KalshiBroker|def get_market_trades|_client\.get_market|\.status|def snapshot" "$R/brokers/kalshi.py" 2>&1 | head -25
echo "=== confirm no feed_health block already in strategies.yaml ==="
grep -n -E "feed_health|mass_exit_threshold|min_positions_for_check|max_consecutive_fetch" /home/azureuser/trading_corp/config/strategies.yaml 2>&1
echo "=== DONE km5 ==="
