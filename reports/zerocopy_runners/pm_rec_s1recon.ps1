# pm_rec_s1recon.ps1 - READ-ONLY fresh reconcile re-check (gate before arming). No latch, no write.
$ErrorActionPreference = 'Continue'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h  = 'azureuser@trading.jacksumner.com'
$sh = 'C:\Users\AA Incorporado\cc\pm_rec_s1recon.sh'
Get-Content -LiteralPath $sh -Raw | ssh -o ConnectTimeout=25 $h "tr -d '\r\357\273\277' | bash"
