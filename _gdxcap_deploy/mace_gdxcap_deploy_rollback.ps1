# ROLLBACK (MACE #2): restore 4 files + re-verify == 7683f59b.
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
Get-Content "$PSScriptRoot\_gdxcap_deploy\rollback_run.sh" -Raw | ssh $h "tr -d '\r\357\273\277' | bash"
