$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$cmd = @'
echo PROBE_START; which sqlite3; which python3; systemctl --no-pager --type=service 2>/dev/null | grep -iE 'trad|corp'; DB=$(find ~/trading_corp -maxdepth 3 -name trading_corp.db 2>/dev/null | head -1); echo "DBPATH=$DB"; ls -la "$DB" 2>/dev/null; sqlite3 "$DB" "SELECT division,COUNT(*) FROM kalshi_round_trips GROUP BY division;" 2>&1 | head -20; echo PROBE_END
'@
$cmd | ssh $h "tr -d '\r\357\273\277' | bash"
