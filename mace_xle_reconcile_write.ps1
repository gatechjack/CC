# RESERVED PROD-DB WRITE -- Jack authorizes + runs. Books the manually-closed XLE 10-30 60/59/70/71
# rung as status=closed (guarded, idempotent, atomic backup). Streams .py to prod venv python.
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
Get-Content "$PSScriptRoot\mace_xle_reconcile_write.py" -Raw | ssh $h "tr -d '\r\357\273\277' | /home/azureuser/trading_corp/venv/bin/python -"
