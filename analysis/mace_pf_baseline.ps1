# BOX-SCRATCH baseline+delta (my authority; zero prod touch). scp a tests-only tar, build a BASE-src
# scratch, and run the SAME pytest set on BOTH the fixed scratch (from mace_pf_scratch.ps1) and the
# base scratch -> prove the failure delta (no NEW failures from the fix).
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$tgz = 'C:\Users\AA Incorporado\cc\mace_pf_baseline.tgz'
$sh  = Join-Path $PSScriptRoot 'mace_pf_baseline.sh'
Write-Host '--- scp baseline tar ---'
scp -o BatchMode=yes -o ConnectTimeout=20 $tgz "${h}:/tmp/mace_pf_baseline.tgz"
Write-Host '--- stream baseline+compare runner ---'
Get-Content $sh -Raw | ssh -o BatchMode=yes -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | bash"
