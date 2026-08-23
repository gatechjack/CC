# pk_pm_own_check.ps1 -- READ-ONLY: ownership/permissions of the PM DB + dir (diagnose the cron write failure).
# No mutation. Run: powershell -ep bypass -f .\pk_pm_own_check.ps1
$ErrorActionPreference = 'Stop'
$enc = New-Object Text.UTF8Encoding($false)
$tf = Join-Path $env:TEMP 'pk_own_box.sh'
$bash = @'
cd /home/azureuser/trading_corp
echo "-- data/ dir --"; ls -ld data
echo "-- PM DB + sidecars --"; ls -l data/prediction_markets.db data/prediction_markets.db-wal data/prediction_markets.db-shm 2>&1
echo "-- who is azureuser --"; id azureuser
echo "-- can azureuser write the db? (simulate: -w test as azureuser) --"
sudo -n -u azureuser test -w data/prediction_markets.db 2>/dev/null && echo "azureuser CAN write db" || echo "azureuser CANNOT write db"
sudo -n -u azureuser test -w data 2>/dev/null && echo "azureuser CAN write data/ dir" || echo "azureuser CANNOT write data/ dir"
echo "-- legacy db owner (context) --"; ls -l data/trading_corp.db 2>/dev/null
'@
$bash = $bash -replace "`r", ""
[IO.File]::WriteAllText($tf, $bash, $enc)
Write-Host "== PM DB ownership / writability (read-only) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
