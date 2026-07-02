# restore_noreboot.ps1 - restore the pre-deploy prod blobs (.bak from apply.ps1) WITHOUT a
# restart. apply.ps1 installed files to disk but NO restart followed, so the engine never
# reloaded the new code -- a disk restore returns prod to the exact pre-deploy state and
# avoids restarting into a stale (~20.5h) RH pickle. Operator paste:
#   powershell -ep bypass -f .\restore_noreboot.ps1
$ErrorActionPreference = "Stop"
$H = "azureuser@trading.jacksumner.com"
Write-Host "=== RESTORE (no restart) ==="
$cmd = @'
cd /home/azureuser/trading_corp; TAG=bak-pre-bidir-2026-07-01; ok=1; for f in trading_corp/main.py trading_corp/agents/divisions/bitunix_sfp_observer.py trading_corp/agents/divisions/bitunix_position_reconciler.py trading_corp/web/sfp_cockpit_view.py trading_corp/web/templates/sfp_cockpit/_state_board.html config/strategies.yaml; do if [ -f "$f.$TAG" ]; then cp "$f.$TAG" "$f"; echo "restored $f"; else echo "NO BACKUP for $f"; ok=0; fi; done; rm -f trading_corp/agents/divisions/bitunix_sfp_research_log.py; echo "removed new research_log module"; echo "== restored md5s (must match preflight snapshot) =="; md5sum trading_corp/main.py trading_corp/agents/divisions/bitunix_sfp_observer.py trading_corp/agents/divisions/bitunix_position_reconciler.py trading_corp/web/sfp_cockpit_view.py trading_corp/web/templates/sfp_cockpit/_state_board.html config/strategies.yaml; echo "== detector (unchanged) =="; md5sum trading_corp/agents/strategies/bitunix_sfp.py | cut -d" " -f1; echo "== engine (must be UNCHANGED: PID 46994, NRestarts 0) =="; systemctl show trading-corp -p MainPID,NRestarts,ActiveState; if [ "$ok" = "1" ]; then echo "RESTORE_OK (no restart performed)"; else echo "WARN: a backup was missing"; fi
'@
$cmd | ssh $H "tr -d '\r'|bash"
Write-Host "=== Verify: 6 restored md5s == preflight snapshot ; detector 91fd7672 ; engine PID 46994 / NRestarts 0 (UNCHANGED). ==="
