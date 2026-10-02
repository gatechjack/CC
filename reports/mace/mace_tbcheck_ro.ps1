$ErrorActionPreference = "Stop"
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = "azureuser@trading.jacksumner.com"
$sh = "C:\Users\AA Incorporado\cc\mace_tbcheck_ro.sh"
Get-Content $sh -Raw | ssh -o BatchMode=yes -o ConnectTimeout=20 $h "tr -d '\r\357\273\277'|bash"
