# MACE OQ-2 BOOT VERIFY (step 3 of 3) - read-only checks + the Board-sanctioned
# halt-latch cycle ARM->HALT->ARM (latch writes only; no orders possible).
$ErrorActionPreference = 'Stop'
$sh = Join-Path $PSScriptRoot '_mace_oq2_verify.sh'
if (-not (Test-Path $sh)) { throw ('script missing: ' + $sh) }
Write-Host 'Running boot verify on tc-prod-vm (~1 min)...'
$out = az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts ('@' + $sh) --query 'value[0].message' -o tsv
$out
$out | Out-File -Encoding utf8 (Join-Path $PSScriptRoot 'mace_oq2_verify_out.txt')
Write-Host ''
Write-Host 'Output saved to mace_oq2_verify_out.txt - paste it back to the agent.'
