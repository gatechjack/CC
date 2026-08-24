$OutputEncoding = New-Object System.Text.UTF8Encoding $false
# PM P3 box-scratch: git-archive the p3 worktree HEAD, scp, stream the read-only probe over ssh (azureuser).
# Operator pastes ONE line:  powershell -ep bypass -f .\pm_p3_boxscratch.ps1
# Reuses the banked P2 harness (Alt-streamer strips CR + the PS-prepended BOM). Read-only to prod.
$h = "azureuser@trading.jacksumner.com"
$wt = "C:\Users\AA Incorporado\cc-pm-p3-wt"
$stage = "C:\Users\AA Incorporado\cc\pm_p3_stage.tgz"
$sh = "C:\Users\AA Incorporado\cc\pm_p3_boxscratch_probe.sh"
if (-not (Test-Path $sh)) { Write-Host "MISSING box script: $sh - STOP"; exit 1 }
Write-Host "[local] git archive p3 worktree HEAD to stage tgz"
git -C $wt archive --format=tar.gz -o $stage HEAD trading_corp tests/prediction_markets tests/conftest.py pyproject.toml
if ($LASTEXITCODE -ne 0) { Write-Host "GIT ARCHIVE FAILED exit $LASTEXITCODE - STOP"; exit 1 }
Write-Host ("[local] HEAD: " + (git -C $wt rev-parse --short HEAD))
Write-Host ("[local] stage sha256: " + (Get-FileHash -Algorithm SHA256 $stage).Hash.ToLower())
Write-Host "[local] scp stage tarball to box..."
scp -o StrictHostKeyChecking=accept-new $stage ($h + ":pm_p3_stage.tgz")
if ($LASTEXITCODE -ne 0) { Write-Host "SCP FAILED exit $LASTEXITCODE - STOP"; exit 1 }
Write-Host "[remote] running P3 box-scratch (read-only to prod)..."
Get-Content -Raw $sh | ssh -o StrictHostKeyChecking=accept-new $h "tr -d '\r\357\273\277' | bash"
Write-Host ("[done] P3 box-scratch finished (ssh exit " + $LASTEXITCODE + ")")
Remove-Item $stage -ErrorAction SilentlyContinue
