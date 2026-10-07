# pm_rec_p1a.ps1 - READ-ONLY: systemd env (KEY_VAULT_URI), pm_account map, UFC cost basis from journal.
$ErrorActionPreference = 'Continue'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h  = 'azureuser@trading.jacksumner.com'
$sh = 'C:\Users\AA Incorporado\cc\pm_rec_p1a.sh'
Get-Content -LiteralPath $sh -Raw | ssh -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | bash"
