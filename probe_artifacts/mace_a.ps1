# RO Part A probe. sqlite -readonly + journalctl only.
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$s = 'C:\Users\AA Incorporado\cc\mace_a.sh'
Get-Content -LiteralPath $s -Raw | ssh -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | bash"
