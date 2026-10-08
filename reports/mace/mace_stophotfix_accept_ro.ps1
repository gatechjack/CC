$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$py = Get-Content (Join-Path $PSScriptRoot 'mace_stophotfix_accept_ro.py') -Raw
$py | ssh -o BatchMode=yes -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | python3 -"
