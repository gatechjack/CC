# pm_rec_s1arm.ps1 - Step 1b: ARM the 57 via pm_cli live-arm --clear-latch (sanctioned arm path) + Step 2 verify.
# Reconcile gate already OPEN (clean). Writes legacy agent_state ONLY via pm_cli. No restart. Authorized by Jack.
$ErrorActionPreference = 'Continue'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h  = 'azureuser@trading.jacksumner.com'
$sh = 'C:\Users\AA Incorporado\cc\pm_rec_s1arm.sh'
Get-Content -LiteralPath $sh -Raw | ssh -o ConnectTimeout=25 $h "tr -d '\r\357\273\277' | bash"
