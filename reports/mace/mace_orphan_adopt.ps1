$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$src = Join-Path $PSScriptRoot 'mace_orphan_adopt.py'
Write-Host '--- RESERVED prod-DB write: streaming mace_orphan_adopt.py to box python3 ---'
Write-Host '--- (run ONLY after the leg-sanity guard is deployed + boot-verified) ---'
$py = Get-Content $src -Raw
$py | ssh -o BatchMode=yes -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | python3 -"
Write-Host '--- done ---'
