$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$tar = Join-Path $PSScriptRoot 'mace_guard_scratch.tar.gz'
$sh = Join-Path $PSScriptRoot 'mace_guard_scratch.sh'
Write-Host '--- scp candidate tar to box /tmp (code-only, no DB) ---'
scp $tar "${h}:/tmp/mace_guard_scratch.tar.gz"
Write-Host '--- streaming box-scratch runner to box bash (live tree untouched) ---'
$s = Get-Content $sh -Raw
$s | ssh $h "tr -d '\r\357\273\277' | bash"
Write-Host '--- done ---'
