$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$tar = Join-Path $PSScriptRoot 'mace_stophotfix_scratch.tar.gz'
$sh = Join-Path $PSScriptRoot 'mace_stophotfix_scratch.sh'
scp -o ConnectTimeout=20 $tar "${h}:/tmp/mace_stophotfix_scratch.tar.gz"
$s = Get-Content $sh -Raw
$s | ssh -o BatchMode=yes -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | bash"
