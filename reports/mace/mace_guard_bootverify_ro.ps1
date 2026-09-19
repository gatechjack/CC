$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$src = Join-Path $PSScriptRoot 'mace_guard_bootverify_ro.py'
Write-Host '--- streaming mace_guard_bootverify_ro.py to box python3 (READ-ONLY) ---'
$py = Get-Content $src -Raw
$py | ssh $h "tr -d '\r\357\273\277' | python3 -"
Write-Host '--- done ---'
