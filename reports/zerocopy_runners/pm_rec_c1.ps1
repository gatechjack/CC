# pm_rec_c1.ps1 - READ-ONLY Condition 1: consumers of close_source + won on the box tree, void precedent, distributions.
$ErrorActionPreference = 'Continue'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h  = 'azureuser@trading.jacksumner.com'
$sh = 'C:\Users\AA Incorporado\cc\pm_rec_c1.sh'
Get-Content -LiteralPath $sh -Raw | ssh -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | bash"
