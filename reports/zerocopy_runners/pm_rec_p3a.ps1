# pm_rec_p3a.ps1 - READ-ONLY: 3.1 reconcile proof (compare, no latch) all 4 accounts + 3.2 armed-set reconstructability probe.
$ErrorActionPreference = 'Continue'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h  = 'azureuser@trading.jacksumner.com'
$sh = 'C:\Users\AA Incorporado\cc\pm_rec_p3a.sh'
Get-Content -LiteralPath $sh -Raw | ssh -o ConnectTimeout=25 $h "tr -d '\r\357\273\277' | bash"
