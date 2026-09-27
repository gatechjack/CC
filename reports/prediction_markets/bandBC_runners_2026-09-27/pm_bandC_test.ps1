# BAND C box-scratch test: tar worktree source -> scp -> py_compile + import + pytest -> cleanup. No live tree touched.
$ErrorActionPreference = "Stop"
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$wt = "C:\Users\AA Incorporado\cc-markpoller-wt"
$tar = Join-Path $PSScriptRoot "pmc_bandC.tar.gz"
$sh = Join-Path $PSScriptRoot "pm_bandC_test.sh"
if (Test-Path $tar) { Remove-Item $tar -Force }
& tar -czf $tar -C $wt trading_corp tests
if (-not (Test-Path $tar)) { Write-Host "tar FAILED"; exit 2 }
Write-Host ("local tar bytes: {0}" -f (Get-Item $tar).Length)
& scp -o ConnectTimeout=20 $tar "${h}:/tmp/pmc_bandC.tar.gz"
if ($LASTEXITCODE -ne 0) { Write-Host "scp FAILED ($LASTEXITCODE)"; exit 2 }
Get-Content $sh -Raw | ssh $h "tr -d '\r\357\273\277' | bash"
Remove-Item $tar -Force -ErrorAction SilentlyContinue
Write-Host "=== pm_bandC_test DONE (exit $LASTEXITCODE) ==="
