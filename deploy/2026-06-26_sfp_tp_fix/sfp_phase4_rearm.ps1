# Phase 2 / Step 4 — HOT RE-ARM bitunix_sfp (auto_execute false -> true). NO restart.
# Operator runner (ONE line):  powershell -ep bypass -f .\sfp_phase4_rearm.ps1
# All logic is in rearm_sfp.py (block-scoped + fail-closed): it asserts the sfp block
# has exactly one auto_execute, currently false, execution_mode live; backs up to
# ~/strategies.yaml.bak-pre-sfp-rearm-2026-06-26; flips; and POST-asserts futures/pead
# unchanged. NO restart needed (_yaml_auto_execute fresh-reads per signal).
$ErrorActionPreference = 'Stop'
$h  = 'azureuser@trading.jacksumner.com'
$py = 'C:\Users\AA Incorporado\Desktop\bitunix_reports\2026-06-26_sfp_tp_fix\rearm_sfp.py'
Write-Host "Uploading + running block-scoped re-arm (fail-closed; prints BEFORE/AFTER)..."
scp "$py" "${h}:rearm_sfp.py"
ssh $h "/home/azureuser/trading_corp/venv/bin/python ~/rearm_sfp.py"
Write-Host ""
Write-Host "If AFTER shows bitunix_sfp auto_execute: true (execution_mode live; futures/pead unchanged) => SFP is ARMED."
Write-Host "No restart needed. Tell the agent to watch for the first SFP->BOS signal."
