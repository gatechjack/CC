# MACE OQ-2 ROLLBACK - restores prod to b11af9b (7 files back + halt partial
# removed) and RESTARTS. Prod-side script refuses inside 15:35-16:00 ET.
# Only valid AFTER mace_oq2_deploy.ps1 wrote /home/azureuser/mace_oq2_bak_20260813.
$ErrorActionPreference = 'Stop'
Write-Host 'Rolling back MACE OQ-2 deploy on tc-prod-vm (~4-5 min incl. restart)...'
$cmd = 'bash /home/azureuser/mace_oq2_bak_20260813/rollback.sh'
$out = az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts $cmd --query 'value[0].message' -o tsv
$out
$out | Out-File -Encoding utf8 (Join-Path $PSScriptRoot 'mace_oq2_rollback_out.txt')
Write-Host ''
Write-Host 'Output saved to mace_oq2_rollback_out.txt - paste it back to the agent.'
