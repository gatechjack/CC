Set-Location "C:\Users\AA Incorporado\cc-2026-07-29-wt"
Get-Content km_v4.sh -Raw | ssh azureuser@trading.jacksumner.com "tr -d '\r'|bash" | Tee-Object -FilePath km_v4_out.txt
