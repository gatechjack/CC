# MACE OQ-2 RESTART (step 2 of 3) - restarts trading-corp + waits for boot.
# The prod-side script REFUSES to run inside 15:35-16:00 ET (guard window).
$ErrorActionPreference = 'Stop'
$sh = Join-Path $PSScriptRoot '_mace_oq2_restart.sh'
if (-not (Test-Path $sh)) { throw ('script missing: ' + $sh) }
Write-Host 'Restarting trading-corp on tc-prod-vm (~4-5 min incl. boot wait)...'
$out = az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts ('@' + $sh) --query 'value[0].message' -o tsv
$out
$out | Out-File -Encoding utf8 (Join-Path $PSScriptRoot 'mace_oq2_restart_out.txt')
Write-Host ''
Write-Host 'Output saved to mace_oq2_restart_out.txt'
Write-Host 'If RESTART RESULT: DONE -> run mace_oq2_verify.ps1'
