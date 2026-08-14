# MACE morning shadow-eval - READ-ONLY confidence check on live quotes.
# Board ruling 2026-08-13: NOT a deploy gate (eval-time credit-floor filter is
# the operative safety). Run during market hours, >= 09:35 ET. Read: each
# active (IBIT/XLE/GDX) should clear its 0.30 x width credit floor.
$ErrorActionPreference = 'Stop'
$sh = Join-Path $PSScriptRoot '_mace_oq2_shadow_am.sh'
if (-not (Test-Path $sh)) { throw ('script missing: ' + $sh) }
Write-Host 'Running READ-ONLY shadow-eval on tc-prod-vm (~1-2 min)...'
$out = az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts ('@' + $sh) --query 'value[0].message' -o tsv
$out
$out | Out-File -Encoding utf8 (Join-Path $PSScriptRoot 'mace_shadow_eval_am_out.txt')
Write-Host ''
Write-Host 'Output saved to mace_shadow_eval_am_out.txt - paste it back to the agent.'
