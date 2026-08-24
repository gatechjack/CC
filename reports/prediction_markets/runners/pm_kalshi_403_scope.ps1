$OutputEncoding = New-Object System.Text.UTF8Encoding $false
# READ-ONLY Kalshi 403 scope: streams an UNAUTHENTICATED probe to the box (no secrets, no auth, no orders).
# Engine-PID bracketed. Operator pastes ONE line:  powershell -ep bypass -f .\pm_kalshi_403_scope.ps1
$h = "azureuser@trading.jacksumner.com"
$sh = "C:\Users\AA Incorporado\cc\pm_kalshi_403_scope.sh"
if (-not (Test-Path $sh)) { Write-Host "MISSING $sh - STOP"; exit 1 }
Write-Host "[box] streaming read-only Kalshi 403 scope (unauthenticated; no secrets)..."
Get-Content -Raw $sh | ssh -o StrictHostKeyChecking=accept-new $h "tr -d '\r\357\273\277' | bash"
Write-Host ("[done] ssh exit " + $LASTEXITCODE)
