# READ-ONLY post-write verify for the XLE reconcile. Run AFTER the write runner. RO.
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
Get-Content "$PSScriptRoot\mace_xle_reconcile_verify_ro.py" -Raw | ssh $h "tr -d '\r\357\273\277' | /home/azureuser/trading_corp/venv/bin/python -"
