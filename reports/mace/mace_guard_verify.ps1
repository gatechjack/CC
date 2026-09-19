$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$tar = Join-Path $PSScriptRoot 'mace_guard_scratch.tar.gz'
$sh = Join-Path $PSScriptRoot 'mace_guard_verify.sh'
Write-Host '--- re-scp candidate tar + focused verbose verify of the new tests ---'
scp $tar "${h}:/tmp/mace_guard_scratch.tar.gz"
$s = Get-Content $sh -Raw
$s | ssh $h "tr -d '\r\357\273\277' | bash"
Write-Host '--- done ---'
