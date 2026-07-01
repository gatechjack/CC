$ErrorActionPreference = 'Continue'
$h = 'azureuser@trading.jacksumner.com'
Write-Host '=== Phase 2a ROLLBACK: restore SFP secret_ref -> bitunix_futures + flat-guarded restart (use if 2a-verify shows 403) ==='
$cmd = @'
cd /home/azureuser/trading_corp || exit 9; DIV=config/divisions.yaml; B="$DIV.bak-pre-2a-2026-06-30"; [ -f "$B" ] || { echo "NO BACKUP $B - cannot rollback"; exit 9; }; DB=/home/azureuser/trading_corp/data/trading_corp.db; open=$(sqlite3 "$DB" "SELECT COUNT(*) FROM paper_trade_record WHERE division='bitunix_sfp' AND result IS NULL AND (extra_json LIKE '%\"execution_mode\": \"live\"%' OR extra_json LIKE '%\"execution_mode\":\"live\"%')" 2>&1); [ "$open" = "0" ] || { echo "SFP NOT FLAT ([$open]) - ABORT rollback restart"; exit 3; }; cp "$B" "$DIV" && echo "divisions.yaml restored from backup"; grep -q "secret_ref: bitunix_futures" "$DIV" && echo "confirmed secret_ref -> bitunix_futures (original key)" || echo "WARN: bitunix_futures not present after restore"; echo "restarting..."; sudo -n systemctl restart trading-corp && sleep 4 && echo "RESTART ISSUED - is-active: $(systemctl is-active trading-corp)"
'@
$cmd | ssh $h "tr -d '\r'|bash"
Write-Host "ssh exit: $LASTEXITCODE  (0=rolled back+restarted; 3=not-flat)"
