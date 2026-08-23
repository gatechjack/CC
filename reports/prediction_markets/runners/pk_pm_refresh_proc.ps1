# pk_pm_refresh_proc.ps1 -- READ-ONLY: is the cron refresh still running? No mutation.
$ErrorActionPreference = 'Stop'
$enc = New-Object Text.UTF8Encoding($false)
$tf = Join-Path $env:TEMP 'pk_rproc_box.sh'
$bash = @'
echo "-- now UTC --"; date -u
echo "-- pm_cli refresh process? --"
ps -eo pid,etimes,cmd | grep -F 'pm_cli.py refresh' | grep -v grep || echo "(no pm_cli refresh process running)"
echo "-- log size/mtime --"; stat -c 'size=%s mtime=%y' /home/azureuser/pm_refresh.log 2>/dev/null || echo "(no log)"
echo "-- WAL size (write activity on PM DB) --"; stat -c '%n %s' /home/azureuser/trading_corp/data/prediction_markets.db-wal 2>/dev/null || echo "(no -wal)"
'@
$bash = $bash -replace "`r", ""
[IO.File]::WriteAllText($tf, $bash, $enc)
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
