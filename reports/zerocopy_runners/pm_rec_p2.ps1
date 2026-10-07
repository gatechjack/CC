# pm_rec_p2.ps1 - Phase 2: online-backup PM DB (leave in place) + book 4 venue-authoritative scalar-close rows + verify.
# Opens ONLY prediction_markets.db RW; never the legacy trading_corp.db. Aborts before any write if preconditions fail.
$ErrorActionPreference = 'Continue'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h  = 'azureuser@trading.jacksumner.com'
$sh = 'C:\Users\AA Incorporado\cc\pm_rec_p2.sh'
Get-Content -LiteralPath $sh -Raw | ssh -o ConnectTimeout=25 $h "tr -d '\r\357\273\277' | bash"
