$OutputEncoding = New-Object System.Text.UTF8Encoding $false
# STAGED - run ONLY in the deliberate window, AFTER pead_part3_deploy.ps1 succeeds.
# Performs the ONE privileged restart (sudo -n systemctl restart) + all-division verify.
$h = "azureuser@trading.jacksumner.com"
$out = "pead_part3_restart_out.txt"
Write-Host "Single restart + all-division verify (deliberate window) ..."
Get-Content .\pead_part3_restart_verify.sh -Raw | ssh $h "tr -d '\r\357\273\277'|bash" | Out-File -Encoding utf8 $out
Write-Host "==== $out ===="
Get-Content $out