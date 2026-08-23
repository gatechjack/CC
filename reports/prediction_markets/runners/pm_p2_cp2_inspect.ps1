$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$sh = "C:\Users\AA Incorporado\cc\pm_p2_cp2_inspect.sh"
if (-not (Test-Path $sh)) { Write-Host "MISSING box script: $sh - STOP"; exit 1 }
Write-Host "[remote] CP2 pre-deploy inspect (READ-ONLY: ls only)..."
Get-Content -Raw $sh | ssh -o StrictHostKeyChecking=accept-new $h "tr -d '\r\357\273\277' | bash"
Write-Host ("[done] pm_p2 CP2 inspect finished (ssh exit " + $LASTEXITCODE + ")")
