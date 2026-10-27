# Stream-only (scratches already on /tmp from the prior runs). Compares the 4 failing-test files on
# BASE src vs FIXED src to prove the failure delta is 0. My authority; zero prod touch.
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$sh = Join-Path $PSScriptRoot 'mace_pf_delta.sh'
Get-Content $sh -Raw | ssh -o BatchMode=yes -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | bash"
