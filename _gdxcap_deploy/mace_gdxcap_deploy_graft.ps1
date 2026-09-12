# GRAFT (MACE #2): base-check box==7683f59b -> write 4 -> verify==c2593e34 + fork-preserve. NO restart, NO FF.
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
Get-Content "$PSScriptRoot\_gdxcap_deploy\graft.sh" -Raw | ssh $h "tr -d '\r\357\273\277' | bash"
