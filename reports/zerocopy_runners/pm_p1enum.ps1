# pm_p1enum.ps1 - READ-ONLY Phase 1: enumerate Kalshi market_result classes across settlement history + per-class fields.
$ErrorActionPreference = 'Continue'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h  = 'azureuser@trading.jacksumner.com'
$sh = 'C:\Users\AA Incorporado\cc\pm_p1enum.sh'
Get-Content -LiteralPath $sh -Raw | ssh -o ConnectTimeout=25 $h "tr -d '\r\357\273\277' | bash"
