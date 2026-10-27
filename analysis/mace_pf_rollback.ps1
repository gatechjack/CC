# RESERVED (box write) -- restore the 3 fix files from the latest graft backup (box==BASE). Stream-only.
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$sh = Join-Path $PSScriptRoot 'mace_pf_rollback.sh'
Get-Content $sh -Raw | ssh -o BatchMode=yes -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | bash"
