$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$tar = Join-Path $PSScriptRoot 'mace_baseline.tar.gz'
$sh = Join-Path $PSScriptRoot 'mace_guard_scratch.sh'
Write-Host '--- BASELINE: scp unmodified-2362db46 tar to box /tmp (same scratch path) ---'
scp $tar "${h}:/tmp/mace_guard_scratch.tar.gz"
Write-Host '--- streaming the SAME box-scratch runner (baseline failure set) ---'
$s = Get-Content $sh -Raw
$s | ssh $h "tr -d '\r\357\273\277' | bash"
Write-Host '--- done ---'
