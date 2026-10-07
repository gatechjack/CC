# pm_rec_s4watch.ps1 - READ-ONLY Step 4: watch fills over several cycles (placed vs rejected per account, is_exit=0).
$ErrorActionPreference = 'Continue'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h  = 'azureuser@trading.jacksumner.com'
$sh = 'C:\Users\AA Incorporado\cc\pm_rec_s4watch.sh'
Get-Content -LiteralPath $sh -Raw | ssh -o ConnectTimeout=25 -o ServerAliveInterval=60 $h "tr -d '\r\357\273\277' | bash"
