$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$sh = "C:\Users\AA Incorporado\cc\pm_p2_cp1_postverify.sh"
if (-not (Test-Path $sh)) { Write-Host "MISSING box script: $sh - STOP"; exit 1 }
Write-Host "[remote] running CP1 post-verify (READ-ONLY: mode=ro DB read + ls)..."
Get-Content -Raw $sh | ssh -o StrictHostKeyChecking=accept-new $h "tr -d '\r\357\273\277' | bash"
Write-Host ("[done] pm_p2 CP1 post-verify finished (ssh exit " + $LASTEXITCODE + ")")
