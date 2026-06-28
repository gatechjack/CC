# SFP watch-emit — upload + additive migration (operator-run; read/write to
# azureuser-owned ~/, NO sudo, NO code deploy). The migration is additive +
# idempotent (CREATE TABLE IF NOT EXISTS) so it is safe with the engine UP.
# Operator runner (ONE line):  powershell -ep bypass -f .\sfp_emit_upload.ps1
# Then APPLY (code, gated):  ssh azureuser@trading.jacksumner.com "bash ~/apply_sfp_watch_emit.sh"
# Then RESTART:              ssh azureuser@trading.jacksumner.com "sudo -n systemctl restart trading-corp"
$ErrorActionPreference = 'Stop'
$src = 'C:\Users\AA Incorporado\Desktop\bitunix_reports\2026-06-26_sfp_watch_emit'
$h   = 'azureuser@trading.jacksumner.com'
Write-Host "[1/3] uploading staged tree + apply + migration ..."
ssh $h "rm -rf ~/sfp_emit_staged"
scp -r "$src\staged" "${h}:sfp_emit_staged"
scp "$src\apply_sfp_watch_emit.sh" "${h}:apply_sfp_watch_emit.sh"
scp "$src\migrate_sfp_watch_state.sql" "${h}:migrate_sfp_watch_state.sql"
ssh $h "tr -d '\r' < ~/apply_sfp_watch_emit.sh > ~/.a && mv ~/.a ~/apply_sfp_watch_emit.sh; tr -d '\r' < ~/migrate_sfp_watch_state.sql > ~/.m && mv ~/.m ~/migrate_sfp_watch_state.sql"
Write-Host "[2/3] running ADDITIVE migration (CREATE TABLE IF NOT EXISTS; engine may stay up) ..."
ssh $h "cd ~/trading_corp && sqlite3 data/trading_corp.db '.read /home/azureuser/migrate_sfp_watch_state.sql'"
Write-Host "[3/3] verify staged md5 (expect detector 5c71a103 / observer 18da45f2) + table present:"
ssh $h "md5sum ~/sfp_emit_staged/trading_corp/agents/strategies/bitunix_sfp.py ~/sfp_emit_staged/trading_corp/agents/divisions/bitunix_sfp_observer.py; cd ~/trading_corp && sqlite3 data/trading_corp.db 'SELECT ''sfp_watch_state rows=''||COUNT(*) FROM sfp_watch_state;'"
Write-Host ""
Write-Host "UPLOADED + MIGRATED. NEXT (code, gated, no restart): ssh $h ""bash ~/apply_sfp_watch_emit.sh"""
