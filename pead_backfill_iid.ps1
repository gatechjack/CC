$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$out = "pead_backfill_out.txt"
Write-Host "Backfilling instrument_id for the 34 open PEAD rows (pure data-add) ..."
Get-Content .\pead_backfill_iid.sh -Raw | ssh $h "tr -d '\r\357\273\277'|bash" | Out-File -Encoding utf8 $out
Write-Host "==== $out ===="
Get-Content $out