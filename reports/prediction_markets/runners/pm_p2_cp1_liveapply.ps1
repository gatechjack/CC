$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$stage = "C:\Users\AA Incorporado\cc\pm_p2_stage.tgz"
$sh = "C:\Users\AA Incorporado\cc\pm_p2_cp1_liveapply.sh"
if (-not (Test-Path $stage)) { Write-Host "MISSING stage tarball: $stage - STOP"; exit 1 }
if (-not (Test-Path $sh))    { Write-Host "MISSING box script: $sh - STOP"; exit 1 }
Write-Host ("[local] stage sha256: " + (Get-FileHash -Algorithm SHA256 $stage).Hash.ToLower())
Write-Host "[local] scp stage tarball to box..."
scp -o StrictHostKeyChecking=accept-new $stage ($h + ":pm_p2_stage.tgz")
if ($LASTEXITCODE -ne 0) { Write-Host "SCP FAILED exit $LASTEXITCODE - STOP"; exit 1 }
Write-Host "[remote] running CP1 STAGE-2 LIVE APPLY (backup -> deploy -> migrate 004 -> rollup on LIVE)..."
Get-Content -Raw $sh | ssh -o StrictHostKeyChecking=accept-new $h "tr -d '\r\357\273\277' | bash"
Write-Host ("[done] pm_p2 CP1 stage-2 live apply finished (ssh exit " + $LASTEXITCODE + ")")
