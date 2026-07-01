# rollback.ps1 - restore the pre-deploy prod blobs (.bak from apply.ps1) + ONE restart.
# LAST RESORT: for a SHORT-side issue the FASTER rollback is the HOT kill-switch (edit
# strategies.yaml side: regime -> long; shorts stop, longs continue, NO restart). Use
# this only to revert the whole code deploy. Operator paste: powershell -ep bypass -f .\rollback.ps1
$ErrorActionPreference = "Stop"
$H = "azureuser@trading.jacksumner.com"
Write-Host "=== ROLLBACK (restore pre-deploy blobs + restart) ==="
$cmd = @'
cd /home/azureuser/trading_corp; TAG=bak-pre-bidir-2026-07-01; ok=1; for f in trading_corp/main.py trading_corp/agents/divisions/bitunix_sfp_observer.py trading_corp/agents/divisions/bitunix_position_reconciler.py trading_corp/web/sfp_cockpit_view.py trading_corp/web/templates/sfp_cockpit/_state_board.html config/strategies.yaml; do if [ -f "$f.$TAG" ]; then cp "$f.$TAG" "$f"; echo "restored $f"; else echo "NO BACKUP for $f (was it applied?)"; ok=0; fi; done; rm -f trading_corp/agents/divisions/bitunix_sfp_research_log.py; echo "removed new research_log module (orphaned after revert)"; echo "detector still:"; md5sum trading_corp/agents/strategies/bitunix_sfp.py | cut -d' ' -f1; echo "SFP open live rows:"; sqlite3 data/trading_corp.db "SELECT COUNT(*) FROM paper_trade_record WHERE division='bitunix_sfp' AND result IS NULL"; if [ "$ok" = "1" ]; then echo "restore OK -> ONE restart"; sudo -n systemctl restart trading-corp; sleep 6; systemctl show trading-corp -p MainPID,ActiveState,SubState; else echo "ABORT restart: a backup was missing"; fi
'@
$cmd | ssh $H "tr -d '\r'|bash"
Write-Host "=== Rolled back to pre-deploy blobs. Verify engine active + reconcilers clean (bootsmoke.ps1 shows the state). ==="
