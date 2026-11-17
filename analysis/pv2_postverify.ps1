# PV2 post-restart verify - streams pv2_postverify.sh via ssh STDIN. READ-ONLY: systemctl show,
# journalctl, sha, sqlite3 mode=ro. No writes, no creds. Run AFTER both services restarted.
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$s = 'C:\Users\AA Incorporado\cc\pv2_postverify.sh'
Get-Content -LiteralPath $s -Raw | ssh -o ConnectTimeout=20 -o BatchMode=yes $h "tr -d '\r\357\273\277' | bash"
