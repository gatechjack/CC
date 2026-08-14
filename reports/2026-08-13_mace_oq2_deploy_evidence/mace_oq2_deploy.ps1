# MACE OQ-2 DEPLOY (step 1 of 3) - swaps 8 files on tc-prod-vm, NO restart.
# Self-gated payload: PRE-GATE (prod==b11af9b) -> STAGE-GATE -> backup+rollback.sh
# -> swap -> POST-GATE -> py_compile. Any gate failure = no swap / clean stop.
$ErrorActionPreference = 'Stop'
$sh = Join-Path $PSScriptRoot '_mace_oq2_deploy_payload.sh'
if (-not (Test-Path $sh)) { throw ('payload missing: ' + $sh) }
Write-Host 'Running deploy payload on tc-prod-vm (as root, ~1 min)...'
$out = az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts ('@' + $sh) --query 'value[0].message' -o tsv
$out
$out | Out-File -Encoding utf8 (Join-Path $PSScriptRoot 'mace_oq2_deploy_out.txt')
Write-Host ''
Write-Host 'Output saved to mace_oq2_deploy_out.txt'
Write-Host 'If DEPLOY RESULT: OK -> run mace_oq2_restart.ps1 (NEVER 15:40-15:58 ET)'
