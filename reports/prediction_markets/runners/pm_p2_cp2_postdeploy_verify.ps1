$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$sh = "C:\Users\AA Incorporado\cc\pm_p2_cp2_postdeploy_verify.sh"
if (-not (Test-Path $sh)) { Write-Host "MISSING box script: $sh - STOP"; exit 1 }
Write-Host "[remote] CP2 post-deploy verify (READ-ONLY: curl/systemctl/ls/sha256/ss/ps)..."
Get-Content -Raw $sh | ssh -o StrictHostKeyChecking=accept-new $h "tr -d '\r\357\273\277' | bash"
Write-Host ("[done] pm_p2 CP2 post-deploy verify finished (ssh exit " + $LASTEXITCODE + ")")
