$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$tar = Join-Path $PSScriptRoot 'mace_stophotfix_graft.tar.gz'
$sh = Join-Path $PSScriptRoot 'mace_stophotfix_graft.sh'
Write-Host '--- scp hotfix graft tar (2 files) to box /tmp ---'
scp -o ConnectTimeout=20 $tar "${h}:/tmp/mace_stophotfix_graft.tar.gz"
Write-Host '--- streaming graft runner (drift-gate + staged-hash gate + backup + apply + re-verify + py_compile + rollback) ---'
$s = Get-Content $sh -Raw
$s | ssh -o BatchMode=yes -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | bash"
Write-Host '--- done (NO restart; run restart_tc.ps1 next, then mace_stophotfix_bootverify_ro.ps1) ---'
