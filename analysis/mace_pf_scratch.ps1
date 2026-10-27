# BOX-SCRATCH acceptance (my authority; zero prod touch). scp the 6-file stage tar, then build a
# scratch copy of the box tree (excl data/venv), overlay the fix, md5+marker gate, py_compile, and
# run the MACE suite + cross-division shared-file tests on the box venv (-p no:pytest_ethereum).
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$tgz = 'C:\Users\AA Incorporado\cc\mace_pf_stage.tgz'
$sh  = Join-Path $PSScriptRoot 'mace_pf_scratch.sh'
Write-Host '--- scp stage tar to box /tmp ---'
scp -o BatchMode=yes -o ConnectTimeout=20 $tgz "${h}:/tmp/mace_pf_stage.tgz"
Write-Host '--- stream scratch runner to box bash ---'
Get-Content $sh -Raw | ssh -o BatchMode=yes -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | bash"
