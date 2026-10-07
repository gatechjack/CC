# pm_rec_p1b.ps1 - READ-ONLY authenticated Kalshi read (ReadOnlyBroker, GET only): market result, position_fp, settlement.
$ErrorActionPreference = 'Continue'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h  = 'azureuser@trading.jacksumner.com'
$sh = 'C:\Users\AA Incorporado\cc\pm_rec_p1b.sh'
Get-Content -LiteralPath $sh -Raw | ssh -o ConnectTimeout=25 $h "tr -d '\r\357\273\277' | bash"
