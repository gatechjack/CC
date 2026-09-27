# BAND B1 -- guarded prod-DB config write (4 ITF per_order_usd_cap NULL -> 50.0), authorized at Checkpoint 1.
# Guarded: drift-gate 4-NULL -> backup all 90 -> scoped UPDATE (rowcount must be 4) -> row-by-row before/after diff.
# No restart. market_types/config read per cycle -> effective next cycle. Engine PID/NRestarts confirmed unchanged.
$ErrorActionPreference = "Stop"
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$sh = Join-Path $PSScriptRoot "pm_bandB1.sh"
if (-not (Test-Path $sh)) { Write-Host "MISSING: $sh"; exit 2 }
Write-Host "=== streaming pm_bandB1.sh (GUARDED WRITE) to $h ==="
Get-Content $sh -Raw | ssh $h "tr -d '\r\357\273\277' | bash"
Write-Host "=== pm_bandB1 DONE (exit $LASTEXITCODE) ==="
