# READ-ONLY PRE-GATE (MACE #2 GDX-cap): box-scratch full suite baseline-diff, isolated, engine untouched.
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
Get-Content "$PSScriptRoot\_gdxcap_deploy\pregate.sh" -Raw | ssh $h "tr -d '\r\357\273\277' | bash"
