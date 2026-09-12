# BACKUP (MACE #2): md5 backup of the 4 files + rollback.sh under ~/.
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
Get-Content "$PSScriptRoot\_gdxcap_deploy\backup.sh" -Raw | ssh $h "tr -d '\r\357\273\277' | bash"
