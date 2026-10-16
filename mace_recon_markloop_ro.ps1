# READ-ONLY MACE mark-feed / manage-loop liveness + traceback characterization (2026-10-15). RO.
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
Get-Content "$PSScriptRoot\mace_recon_markloop_ro.py" -Raw | ssh $h "tr -d '\r\357\273\277' | /home/azureuser/trading_corp/venv/bin/python -"
