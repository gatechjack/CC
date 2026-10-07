$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$tar = Join-Path $PSScriptRoot 'mace_exitredesign_scratch.tar.gz'
$sh = Join-Path $PSScriptRoot 'mace_exitredesign_scratch.sh'
Write-Host '--- scp candidate tar to box /tmp (code-only, no DB; live tree untouched) ---'
scp -o ConnectTimeout=20 $tar "${h}:/tmp/mace_exitredesign_scratch.tar.gz"
Write-Host '--- streaming box-scratch runner (RO throwaway) ---'
$s = Get-Content $sh -Raw
$s | ssh -o BatchMode=yes -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | bash"
Write-Host '--- done ---'
