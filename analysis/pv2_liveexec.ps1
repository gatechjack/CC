# PV2 live-verify exec: (1) stream pv2_live_verify.py into box scratch, (2) run it with the box venv
# python. READ-ONLY: public v2 GETs (throttled, budgeted) + sqlite mode=ro. No creds, no writes.
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
Get-Content -LiteralPath 'C:\Users\AA Incorporado\cc\pv2_live_verify.py' -Raw | ssh -o ConnectTimeout=20 -o BatchMode=yes $h "tr -d '\r\357\273\277' > /home/azureuser/pv2_scratch/pv2_live_verify.py"
if ($LASTEXITCODE -ne 0) { throw "stream failed: $LASTEXITCODE" }
ssh -o ConnectTimeout=20 -o BatchMode=yes $h "cd /home/azureuser/pv2_scratch && PYTHONPATH=. timeout 480 /home/azureuser/trading_corp/venv/bin/python pv2_live_verify.py"
