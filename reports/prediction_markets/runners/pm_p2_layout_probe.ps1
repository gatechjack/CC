$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$sh = "C:\Users\AA Incorporado\cc\_p2_layout_probe.sh"
if (-not (Test-Path $sh)) { Write-Host "MISSING $sh - STOP"; exit 1 }
Write-Host "[remote] CP2 Phase-2 LAYOUT PROBE (READ-ONLY: cat unit / import-resolution / ls / git / curl healthz)..."
Get-Content -Raw $sh | ssh -o StrictHostKeyChecking=accept-new $h "tr -d '\r\357\273\277' | bash"
Write-Host ("[done] layout probe finished (ssh exit " + $LASTEXITCODE + ")")
