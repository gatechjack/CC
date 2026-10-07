# pm_rec_s3shards.ps1 - READ-ONLY Step 3: shard balances per account per shard, name thin accounts.
$ErrorActionPreference = 'Continue'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h  = 'azureuser@trading.jacksumner.com'
$sh = 'C:\Users\AA Incorporado\cc\pm_rec_s3shards.sh'
Get-Content -LiteralPath $sh -Raw | ssh -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | bash"
