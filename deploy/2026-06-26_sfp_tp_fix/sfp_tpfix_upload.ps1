# Upload the staged SFP TP-fix to prod (read/write to azureuser-owned ~/, NO sudo, NO deploy).
# Operator runner (ONE line):  powershell -ep bypass -f .\sfp_tpfix_upload.ps1
# This ONLY uploads + verifies. The APPLY is a separate operator step:
#   ssh azureuser@trading.jacksumner.com "bash ~/apply_sfp_tp_fix.sh"
$ErrorActionPreference = 'Stop'
$src = 'C:\Users\AA Incorporado\Desktop\bitunix_reports\2026-06-26_sfp_tp_fix'
$h   = 'azureuser@trading.jacksumner.com'
Write-Host "Uploading staged SFP TP-fix from $src to $h ..."
ssh $h "rm -rf ~/sfp_tpfix_staged"
scp -r "$src\staged" "${h}:sfp_tpfix_staged"
scp "$src\apply_sfp_tp_fix.sh" "${h}:apply_sfp_tp_fix.sh"
ssh $h "tr -d '\r' < ~/apply_sfp_tp_fix.sh > ~/.a && mv ~/.a ~/apply_sfp_tp_fix.sh"
Write-Host "--- remote staged tree + md5 (expect observer db831daf / main 1069a6db) ---"
ssh $h "find ~/sfp_tpfix_staged -type f; md5sum ~/sfp_tpfix_staged/trading_corp/agents/divisions/bitunix_sfp_observer.py ~/sfp_tpfix_staged/trading_corp/main.py"
Write-Host ""
Write-Host "UPLOADED. Next (apply, no restart): ssh $h ""bash ~/apply_sfp_tp_fix.sh"""
