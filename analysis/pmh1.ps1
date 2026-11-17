# PM HEALTH RO probe 1 - streams pmh1.sh to the box via ssh STDIN. READ-ONLY:
# df, systemctl show, git, journalctl, sqlite3 mode=ro. No writes, no creds, no auth/venue calls.
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$s = 'C:\Users\AA Incorporado\cc\pmh1.sh'
Get-Content -LiteralPath $s -Raw | ssh -o ConnectTimeout=20 -o BatchMode=yes $h "tr -d '\r\357\273\277' | bash"
