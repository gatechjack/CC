# PM HEALTH RO probe 4 - streams pmh4.sh via ssh STDIN. READ-ONLY. No writes/creds/auth calls.
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$s = 'C:\Users\AA Incorporado\cc\pmh4.sh'
Get-Content -LiteralPath $s -Raw | ssh -o ConnectTimeout=20 -o BatchMode=yes $h "tr -d '\r\357\273\277' | bash"
