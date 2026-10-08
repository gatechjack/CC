# pm_hc1.ps1 - READ-ONLY health check part 1 (DB): fills, first-fills, leg-audit, P&L, exposure, shards, liveness.
$ErrorActionPreference = 'Continue'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h  = 'azureuser@trading.jacksumner.com'
$sh = 'C:\Users\AA Incorporado\cc\pm_hc1.sh'
Get-Content -LiteralPath $sh -Raw | ssh -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | bash"
