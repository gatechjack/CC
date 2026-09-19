$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$tar = Join-Path $PSScriptRoot 'mace_guard_graft.tar.gz'
$sh = Join-Path $PSScriptRoot 'mace_guard_graft.sh'
Write-Host '--- scp graft tar (5 files) to box /tmp ---'
scp $tar "${h}:/tmp/mace_guard_graft.tar.gz"
Write-Host '--- streaming graft runner (drift-gate + backup + cp + re-verify + py_compile) ---'
$s = Get-Content $sh -Raw
$s | ssh $h "tr -d '\r\357\273\277' | bash"
Write-Host '--- done (NO restart performed; run restart_tc.ps1 next, then bootverify) ---'
