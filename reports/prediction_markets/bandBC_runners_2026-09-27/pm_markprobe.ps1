# READ-ONLY probe of the Kalshi single-market endpoint shape (GET /markets/{ticker}).
$ErrorActionPreference = "Stop"
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$sh = Join-Path $PSScriptRoot "pm_markprobe.sh"
if (-not (Test-Path $sh)) { Write-Host "MISSING: $sh"; exit 2 }
Get-Content $sh -Raw | ssh $h "tr -d '\r\357\273\277' | bash"
Write-Host "=== pm_markprobe DONE (exit $LASTEXITCODE) ==="
