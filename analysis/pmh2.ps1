# PM HEALTH RO probe 2 - streams pmh2.sh via ssh STDIN. READ-ONLY sqlite3 mode=ro. No writes/creds/auth calls.
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$s = 'C:\Users\AA Incorporado\cc\pmh2.sh'
Get-Content -LiteralPath $s -Raw | ssh -o ConnectTimeout=20 -o BatchMode=yes $h "tr -d '\r\357\273\277' | bash"
