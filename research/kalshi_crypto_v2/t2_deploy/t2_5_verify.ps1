# T2 step 5: acceptance gate - heartbeat + quotes + signals + recent logs (READ-ONLY).
# Run ~90s after start. Read the sheet's PASS / STOP-if against this output.
$ErrorActionPreference = 'Stop'
$h = 'azureuser@trading.jacksumner.com'
$bash = @'
cd /home/azureuser/trading_corp
echo "--- heartbeat (want: new row per ~30s, rows_index=4, rows_quotes>0, index_ws_connected=1, alarm=0) ---"
sqlite3 -readonly data/trading_corp.db "SELECT cycle_id,rows_index,rows_quotes,rows_signals,index_ws_connected,alarm,datetime(ts_ms/1000,'unixepoch') AS t FROM kcv2_heartbeat ORDER BY id DESC LIMIT 5;"
echo "--- quotes (want: COUNT>0, SUM ~= COUNT) ---"
sqlite3 -readonly data/trading_corp.db "SELECT COUNT(*), SUM(sum_to_1_ok) FROM kcv2_quotes;"
echo "--- signals (want: non-null computed_bar_ts_ms) ---"
sqlite3 -readonly data/trading_corp.db "SELECT asset,state,computed_bar_ts_ms FROM kcv2_signals ORDER BY id DESC LIMIT 8;"
echo "--- recent logs (want: WS connected + cycle lines, no tracebacks/KalshiAuthError) ---"
sudo -n journalctl -u trading-corp-kcv2-observer -n 25 --no-pager
'@
$bash | ssh $h "tr -d '\r' | bash"
