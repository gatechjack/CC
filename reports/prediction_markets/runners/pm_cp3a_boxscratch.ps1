$OutputEncoding = New-Object System.Text.UTF8Encoding $false
# PM CP3a box-scratch: git-archive the cp3a worktree HEAD, scp, stream the read-only probe over ssh (azureuser).
# Operator pastes ONE line:  powershell -ep bypass -f .\pm_cp3a_boxscratch.ps1
# Reuses the banked P2/P3 harness (Alt-streamer strips CR + the PS-prepended BOM). Read-only to prod.
$h = "azureuser@trading.jacksumner.com"
$wt = "C:\Users\AA Incorporado\cc-cp3a"
$stage = "C:\Users\AA Incorporado\cc\pm_cp3a_stage.tgz"
$sh = "C:\Users\AA Incorporado\cc-cp3a\reports\prediction_markets\runners\pm_cp3a_boxscratch_probe.sh"
if (-not (Test-Path $sh)) { Write-Host "MISSING box script: $sh - STOP"; exit 1 }
Write-Host "[local] git archive cp3a worktree HEAD to stage tgz"
git -C $wt archive --format=tar.gz -o $stage HEAD trading_corp tests/prediction_markets tests/conftest.py pyproject.toml config/pm_farm_pin_provenance.yaml
if ($LASTEXITCODE -ne 0) { Write-Host "GIT ARCHIVE FAILED exit $LASTEXITCODE - STOP"; exit 1 }
Write-Host ("[local] HEAD: " + (git -C $wt rev-parse --short HEAD))
Write-Host ("[local] stage sha256: " + (Get-FileHash -Algorithm SHA256 $stage).Hash.ToLower())
Write-Host "[local] scp stage tarball to box..."
scp -o StrictHostKeyChecking=accept-new $stage ($h + ":pm_cp3a_stage.tgz")
if ($LASTEXITCODE -ne 0) { Write-Host "SCP FAILED exit $LASTEXITCODE - STOP"; exit 1 }
Write-Host "[remote] running CP3a box-scratch (read-only to prod)..."
Get-Content -Raw $sh | ssh -o StrictHostKeyChecking=accept-new $h "tr -d '\r\357\273\277' | bash"
Write-Host ("[done] CP3a box-scratch finished (ssh exit " + $LASTEXITCODE + ")")
Remove-Item $stage -ErrorAction SilentlyContinue
