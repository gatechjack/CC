$OutputEncoding = New-Object System.Text.UTF8Encoding $false
# READ-ONLY: distinguish stale-key (a) vs order-path defect (b) for poly_kalshi's 401. scp the analysis,
# stream the box runner (hashes the engine's KALSHI-KAREN PEM tempfile vs current vault; timestamp fallback).
# NO orders, NO restart, NO code/config. Secrets by fingerprint only. Engine-PID bracketed.
# Operator pastes ONE line:  powershell -ep bypass -f .\pm_kalshi_keydiag.ps1
$h = "azureuser@trading.jacksumner.com"
$py = "C:\Users\AA Incorporado\cc\pm_kalshi_keydiag.py"
$sh = "C:\Users\AA Incorporado\cc\pm_kalshi_keydiag_box.sh"
if (-not (Test-Path $py)) { Write-Host "MISSING $py - STOP"; exit 1 }
if (-not (Test-Path $sh)) { Write-Host "MISSING $sh - STOP"; exit 1 }
Write-Host "[local] scp read-only key-diag to box..."
scp -o StrictHostKeyChecking=accept-new "$py" ($h + ":pm_kalshi_keydiag.py")
if ($LASTEXITCODE -ne 0) { Write-Host "SCP FAILED - STOP"; exit 1 }
Write-Host "[box] running read-only key-diag (engine tempfile vs vault, by hash; NO orders/restart)..."
Get-Content -Raw $sh | ssh -o StrictHostKeyChecking=accept-new $h "tr -d '\r\357\273\277' | bash"
Write-Host ("[done] ssh exit " + $LASTEXITCODE)
