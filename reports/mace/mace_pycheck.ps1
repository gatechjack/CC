$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$a = Join-Path $PSScriptRoot 'mace_orphan_adopt.py'
$b = Join-Path $PSScriptRoot 'mace_legsanity_bootverify_ro.py'
scp -o ConnectTimeout=20 $a $b "${h}:/tmp/"
ssh -o BatchMode=yes -o ConnectTimeout=20 $h "python3 -m py_compile /tmp/mace_orphan_adopt.py /tmp/mace_legsanity_bootverify_ro.py && echo PYCOMPILE_OK; rm -f /tmp/mace_orphan_adopt.py /tmp/mace_legsanity_bootverify_ro.py; rm -rf /tmp/__pycache__"
