set -u
DB=/home/azureuser/trading_corp/data/trading_corp.db
RO="sqlite3 -readonly $DB"
echo "=== updated_ts (roster keys) ==="
$RO "SELECT key||' ts='||updated_ts||' len='||length(value_json) FROM agent_state WHERE agent='polymarket_copy_trader' AND key IN ('selected_whales','pinned_whales','watch_only_whales') ORDER BY key;"
echo "=== llllllII open/unresolved copied positions (flatten impact) ==="
$RO "SELECT 'unresolved_rows='||COUNT(*) FROM polymarket_round_trips WHERE division='polymarket_copy_trading' AND json_extract(extra_json,'\$.whale_wallet')='0x7714c16f86bcfdba47bfcb161dc39a2a1ff2b814' AND resolved_ts IS NULL;"
echo "=== sizing config live (conviction enabled?) ==="
grep -A14 'polymarket_copy' /home/azureuser/trading_corp/config/strategies.yaml 2>/dev/null | grep -iE 'sizing|conviction|enabled|per_trade|bankroll|min_size|max_size|flat' | head -20
echo "=== any scheduled polymarket refresh? (cron/systemd timer) ==="
( crontab -l 2>/dev/null; ls /etc/cron.d 2>/dev/null; systemctl list-timers 2>/dev/null | grep -i poly ) | grep -iE 'poly|refresh_polymarket' | head -5 || echo "none-found"
echo "=== DONE ==="
