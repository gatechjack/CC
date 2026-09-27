# BAND C GRAFT ORCHESTRATOR (DEPLOY -- Board-authorized at Checkpoint 2 only).
# Tars the 3 changed pm_web files from the worktree, scps, streams the guarded graft (drift-gate box==BASE ->
# stage==TARGET -> backup -> apply -> verify live==TARGET -> py_compile -> rollback-all on any failure).
# Does NOT restart pm_web and does NOT FF prod-live -- those are the Board's next two steps.
$ErrorActionPreference = "Stop"
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$wt = "C:\Users\AA Incorporado\cc-markpoller-wt"
$tar = Join-Path $PSScriptRoot "pmc_bandC_deploy.tar.gz"
$sh = Join-Path $PSScriptRoot "pm_bandC_graft.sh"
if (Test-Path $tar) { Remove-Item $tar -Force }
& tar -czf $tar -C $wt trading_corp/prediction_markets/web/marks.py trading_corp/prediction_markets/web/poller.py trading_corp/prediction_markets/web/app.py
if (-not (Test-Path $tar)) { Write-Host "tar FAILED"; exit 2 }
Write-Host ("tar bytes: {0}" -f (Get-Item $tar).Length)
& scp -o ConnectTimeout=20 $tar "${h}:/tmp/pmc_bandC_deploy.tar.gz"
if ($LASTEXITCODE -ne 0) { Write-Host "scp FAILED ($LASTEXITCODE)"; exit 2 }
Get-Content $sh -Raw | ssh $h "tr -d '\r\357\273\277' | bash"
Remove-Item $tar -Force -ErrorAction SilentlyContinue
Write-Host "=== pm_bandC_graft DONE (exit $LASTEXITCODE) ==="
