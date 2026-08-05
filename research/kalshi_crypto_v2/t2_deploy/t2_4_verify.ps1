# T2 step 4: acceptance gate - heartbeat + quotes + signals + recent logs, via az run-command (root,
# NO sudo). Run ~90s after step 3. Apply the sheet's PASS / STOP-if to this output.
$ErrorActionPreference = 'Stop'
$rg = 'RG-SHARED-PROD'; $vm = 'tc-prod-vm'
$bash = @'
cd /home/azureuser/trading_corp
echo "--- heartbeat (want: new row per ~30s, rows_index=4, rows_quotes>0, index_ws_connected=1, alarm=0) ---"
sqlite3 -readonly data/trading_corp.db "SELECT cycle_id,rows_index,rows_quotes,rows_signals,index_ws_connected,alarm,datetime(ts_ms/1000,'unixepoch') FROM kcv2_heartbeat ORDER BY id DESC LIMIT 5;"
echo "--- quotes (want COUNT>0, SUM ~= COUNT) ---"
sqlite3 -readonly data/trading_corp.db "SELECT COUNT(*),SUM(sum_to_1_ok) FROM kcv2_quotes;"
echo "--- signals (want non-null computed_bar_ts_ms) ---"
sqlite3 -readonly data/trading_corp.db "SELECT asset,state,computed_bar_ts_ms FROM kcv2_signals ORDER BY id DESC LIMIT 8;"
echo "--- logs (want WS connected + cycle lines, no tracebacks/KalshiAuthError) ---"
journalctl -u trading-corp-kcv2-observer -n 25 --no-pager
'@
$bash = $bash -replace "`r", ""
(az vm run-command invoke -g $rg -n $vm --command-id RunShellScript --scripts $bash | ConvertFrom-Json).value[0].message
