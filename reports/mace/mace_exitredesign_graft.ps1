$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$tar = Join-Path $PSScriptRoot 'mace_exitredesign_graft.tar.gz'
$sh = Join-Path $PSScriptRoot 'mace_exitredesign_graft.sh'
Write-Host '--- scp graft tar (5 files) to box /tmp ---'
scp -o ConnectTimeout=20 $tar "${h}:/tmp/mace_exitredesign_graft.tar.gz"
Write-Host '--- streaming graft runner (drift-gate + staged-hash gate + backup + apply + re-verify + py_compile + rollback) ---'
$s = Get-Content $sh -Raw
$s | ssh -o BatchMode=yes -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | bash"
Write-Host '--- done (NO restart performed; run restart_tc.ps1 next, then mace_exitredesign_bootverify_ro.ps1) ---'
