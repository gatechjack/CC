# READ-ONLY Band B before-state: schema, 4 ITF rows, snapshot hashes, inning slug count.
$ErrorActionPreference = "Stop"
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$sh = Join-Path $PSScriptRoot "pm_bandB0.sh"
if (-not (Test-Path $sh)) { Write-Host "MISSING: $sh"; exit 2 }
Write-Host "=== streaming pm_bandB0.sh to $h (read-only) ==="
Get-Content $sh -Raw | ssh $h "tr -d '\r\357\273\277' | bash"
Write-Host "=== pm_bandB0 DONE (exit $LASTEXITCODE) ==="
