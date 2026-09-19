$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$src = Join-Path $PSScriptRoot 'mace_xle_reset.py'
Write-Host '--- RESERVED prod-DB write: XLE 2026-10-30 rung CLOSING -> open (run AFTER guard deploy+bootverify) ---'
$py = Get-Content $src -Raw
$py | ssh $h "tr -d '\r\357\273\277' | python3 -"
Write-Host '--- done ---'
