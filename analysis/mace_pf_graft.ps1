# RESERVED (box write) -- Jack runs after authorization. scp the 3-file src tar + stream the drift-
# gated graft. Does NOT restart (restart is the separate reserved restart_tc.ps1 step).
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$tgz = 'C:\Users\AA Incorporado\cc\mace_pf_src.tgz'
$sh  = Join-Path $PSScriptRoot 'mace_pf_graft.sh'
Write-Host '--- scp src tar to box /tmp ---'
scp -o BatchMode=yes -o ConnectTimeout=20 $tgz "${h}:/tmp/mace_pf_src.tgz"
Write-Host '--- stream drift-gated graft to box bash ---'
Get-Content $sh -Raw | ssh -o BatchMode=yes -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | bash"
