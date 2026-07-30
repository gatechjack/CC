Set-Location "C:\Users\AA Incorporado\cc-2026-07-29-wt"
Get-Content km_v3.sh -Raw | ssh azureuser@trading.jacksumner.com "tr -d '\r'|bash" | Tee-Object -FilePath km_v3_out.txt
