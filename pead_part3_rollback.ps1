$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$out = "pead_part3_rollback_out.txt"
Write-Host "Rolling back Part 3 files to pre-deploy baseline (no restart happened) ..."
Get-Content .\pead_part3_rollback.sh -Raw | ssh $h "tr -d '\r\357\273\277'|bash" | Out-File -Encoding utf8 $out
Write-Host "==== $out ===="
Get-Content $out