# pm_p3verify.ps1 - READ-ONLY Phase 3.2/3.3 boot-verify: new PID/ActiveEnter, settlement.py==TARGET, boot-reconcile,
# arm survival, divisions wired, first-scan vs prediction. Waits for the new boot. No write, no restart.
$ErrorActionPreference = 'Continue'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h  = 'azureuser@trading.jacksumner.com'
$sh = 'C:\Users\AA Incorporado\cc\pm_p3verify.sh'
Get-Content -LiteralPath $sh -Raw | ssh -o ConnectTimeout=25 -o ServerAliveInterval=60 $h "tr -d '\r\357\273\277' | bash"
