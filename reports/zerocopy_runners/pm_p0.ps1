# pm_p0.ps1 - READ-ONLY Phase 0: box engine file CR-sha + settlement.py + schema/migrations + re-book idempotency.
$ErrorActionPreference = 'Continue'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h  = 'azureuser@trading.jacksumner.com'
$sh = 'C:\Users\AA Incorporado\cc\pm_p0.sh'
Get-Content -LiteralPath $sh -Raw | ssh -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | bash"
