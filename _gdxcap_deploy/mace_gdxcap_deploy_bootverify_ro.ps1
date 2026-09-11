# READ-ONLY BOOT-VERIFY (MACE #2): config_hash bfde856f, winner pricing live, fork preserved, MACE-scoped.
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
Get-Content "$PSScriptRoot\_gdxcap_deploy\bootverify.py" -Raw | ssh $h "tr -d '\r\357\273\277' | /home/azureuser/trading_corp/venv/bin/python -"
