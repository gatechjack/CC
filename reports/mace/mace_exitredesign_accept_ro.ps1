$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$src = Join-Path $PSScriptRoot 'mace_exitredesign_accept_ro.py'
Write-Host '--- streaming mace_exitredesign_accept_ro.py to box python3 (READ-ONLY; run at/after Monday 09:35 ET) ---'
$py = Get-Content $src -Raw
$py | ssh -o BatchMode=yes -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | python3 -"
Write-Host '--- done ---'
