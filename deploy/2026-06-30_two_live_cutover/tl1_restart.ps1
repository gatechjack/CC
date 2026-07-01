$ErrorActionPreference = 'Continue'
$h = 'azureuser@trading.jacksumner.com'
Write-Host '=== two-live Phase 1: FLAT-GUARDED RESTART (optional - your hard reboot also serves as the restart) ==='
$cmd = @'
DB=/home/azureuser/trading_corp/data/trading_corp.db; q="SELECT COUNT(*) FROM paper_trade_record WHERE division='bitunix_sfp' AND result IS NULL AND (extra_json LIKE '%\"execution_mode\": \"live\"%' OR extra_json LIKE '%\"execution_mode\":\"live\"%')"; open=$(sudo -n sqlite3 "$DB" "$q" 2>&1); if [ "$open" = "0" ]; then echo "SFP FLAT (0 open live rows) - restarting trading-corp"; sudo -n systemctl restart trading-corp && sleep 3 && echo "RESTART ISSUED - is-active: $(systemctl is-active trading-corp)"; else echo "SFP NOT FLAT or flat-check failed (got: [$open]) - ABORT, NO restart"; exit 3; fi
'@
$cmd | ssh $h "tr -d '\r'|bash"
Write-Host "ssh exit: $LASTEXITCODE  (0=restarted; 3=not-flat abort/no restart)"
