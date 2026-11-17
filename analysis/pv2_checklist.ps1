# PV2 checklist probe - streams pv2_checklist.sh via ssh STDIN. READ-ONLY sqlite3 mode=ro. No writes/creds.
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$s = 'C:\Users\AA Incorporado\cc\pv2_checklist.sh'
Get-Content -LiteralPath $s -Raw | ssh -o ConnectTimeout=20 -o BatchMode=yes $h "tr -d '\r\357\273\277' | bash"
