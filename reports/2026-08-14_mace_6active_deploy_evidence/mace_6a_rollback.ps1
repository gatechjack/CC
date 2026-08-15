# MACE 6-active ROLLBACK — restores the 3 runtime files to 3772d5b (pre-6active)
# and RESTARTS. Server-side rollback.sh refuses inside 15:35-16:00 ET.
# Reverts: build_condor width-fallback fix + 6-active enable + FXI ex-div/blackout
# -> back to 3-active [IBIT,XLE,GDX] config_hash e9c0499886c4, engine restart.
# Only valid AFTER the 2026-08-14 ~23:38 UTC deploy wrote the backup dir.
$ErrorActionPreference = 'Stop'
Write-Host 'Rolling back MACE 6-active deploy on tc-prod-vm (~4-5 min incl. restart)...'
$cmd = 'bash /home/azureuser/mace_6active_bak_20260814_233644/rollback.sh'
$out = az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts $cmd --query 'value[0].message' -o tsv
$out
$out | Out-File -Encoding utf8 (Join-Path $PSScriptRoot 'mace_6a_rollback_out.txt')
Write-Host ''
Write-Host 'Output saved to mace_6a_rollback_out.txt - paste it back to the agent.'
