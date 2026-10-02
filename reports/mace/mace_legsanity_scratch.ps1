$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$tar = Join-Path $PSScriptRoot 'mace_legsanity_scratch.tar.gz'
$sh = Join-Path $PSScriptRoot 'mace_guard_scratch.sh'
Write-Host '--- scp candidate tar to box /tmp (code-only, no DB) ---'
scp -o ConnectTimeout=20 $tar "${h}:/tmp/mace_guard_scratch.tar.gz"
Write-Host '--- streaming box-scratch runner (live tree untouched) ---'
$s = Get-Content $sh -Raw
$s | ssh -o BatchMode=yes -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | bash"
Write-Host '--- done ---'
