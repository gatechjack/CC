$OutputEncoding = New-Object System.Text.UTF8Encoding $false
# STAGED - run ONLY in the deliberate deploy window. Deploys 2 files + Gate-A; does NOT restart.
$h = "azureuser@trading.jacksumner.com"
$wt = "C:/Users/AA Incorporado/cc-pead-rename-defense-2026-08-26-wt"
$out = "pead_part3_deploy_out.txt"
Write-Host "Staging Part 3 files + Gate-A (no restart) ..."
ssh $h "mkdir -p ~/pead_part3_stage"
scp "$wt/trading_corp/brokers/robinhood.py" "${h}:pead_part3_stage/robinhood.py"
scp "$wt/trading_corp/agents/strategies/pead_strategy.py" "${h}:pead_part3_stage/pead_strategy.py"
Get-Content .\pead_part3_deploy.sh -Raw | ssh $h "tr -d '\r\357\273\277'|bash" | Out-File -Encoding utf8 $out
Write-Host "==== $out ===="
Get-Content $out