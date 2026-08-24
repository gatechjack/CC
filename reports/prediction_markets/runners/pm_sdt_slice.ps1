$OutputEncoding = New-Object System.Text.UTF8Encoding $false
# READ-ONLY SDTrading mlb three-way slice (moneyline/total/spread) -- Task 1, investigation pt2.
# scp the analysis + run mode=ro as azureuser; engine-PID bracketed; analysis removed after. No writes/restart/root.
# Operator pastes ONE line:  powershell -ep bypass -f .\pm_sdt_slice.ps1
$h = "azureuser@trading.jacksumner.com"
$py = "C:\Users\AA Incorporado\cc\pm_sdt_slice_analysis.py"
if (-not (Test-Path $py)) { Write-Host "MISSING $py - STOP"; exit 1 }
Write-Host "[local] scp read-only slice analysis to box..."
scp -o StrictHostKeyChecking=accept-new "$py" ($h + ":pm_sdt_slice_analysis.py")
if ($LASTEXITCODE -ne 0) { Write-Host "SCP FAILED - STOP"; exit 1 }
Write-Host "[box] running read-only slice (mode=ro; engine bracketed)..."
$cmd = @'
P0=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null); echo ENGINE_BEFORE=$P0; cd /home/azureuser/trading_corp && PYTHONPATH=/home/azureuser/trading_corp venv/bin/python /home/azureuser/pm_sdt_slice_analysis.py 2>&1; P1=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null); echo ENGINE_AFTER=$P1; rm -f /home/azureuser/pm_sdt_slice_analysis.py; if [ "$P0" = "$P1" ]; then echo ENGINE_UNCHANGED=GOOD; else echo ENGINE_CHANGED=INVESTIGATE; fi
'@
$cmd | ssh -o StrictHostKeyChecking=accept-new $h "tr -d '\r\357\273\277' | bash"
Write-Host ("[done] ssh exit " + $LASTEXITCODE)
