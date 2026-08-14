$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$cmd = @'
echo "=== SVC_TC ==="; systemctl is-active trading-corp.service; systemctl show trading-corp.service -p MainPID -p ActiveEnterTimestamp -p NRestarts; echo "=== SVC_AUDIT_REALITY ==="; systemctl status tc-audit-reality.service --no-pager -l 2>&1 | head -24; echo "=== SVC_KCV2_OBS ==="; systemctl is-active trading-corp-kcv2-observer.service; echo "=== JOURNAL_ACCESS ==="; journalctl -u trading-corp.service -n 1 --no-pager 2>&1 | head -3; echo "=== JOURNAL_ERR_COUNT_SINCE_0805 ==="; journalctl -u trading-corp.service --since '2026-08-05' --no-pager 2>&1 | grep -ciE 'error|exception|traceback'; echo "=== JOURNAL_KALSHI_ERRS_TAIL ==="; journalctl -u trading-corp.service --since '2026-08-05' --no-pager 2>&1 | grep -i kalshi | grep -iE 'error|exception|traceback|fail|starv|denied' | tail -30; echo "=== JOURNAL_KALSHI_RESOLVER_TAIL ==="; journalctl -u trading-corp.service --since '2026-08-05' --no-pager 2>&1 | grep -iE 'resolver|book|settl' | grep -i kalshi | tail -20; echo "=== DONE_SVC ==="
'@
$cmd | ssh $h "tr -d '\r\357\273\277' | bash"
