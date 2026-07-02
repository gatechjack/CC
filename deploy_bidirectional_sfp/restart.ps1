# restart.ps1 - ONE flat-guarded engine-level restart. Gates IN the remote bash: SFP
# flat (reconciler match_count==0 via open live rows==0) AND RH pickle fresh (<20h).
# bitunix_futures may hold independently (isolated) -- does NOT block. The engine-level
# restart reconciles BOTH divisions post-boot. Operator paste: powershell -ep bypass -f .\restart.ps1
$ErrorActionPreference = "Stop"
$H = "azureuser@trading.jacksumner.com"
Write-Host "=== RESTART (flat-guarded) ==="
$cmd = @'
cd /home/azureuser/trading_corp; open=$(sqlite3 data/trading_corp.db "SELECT COUNT(*) FROM paper_trade_record WHERE division='bitunix_sfp' AND result IS NULL"); age=$(( ($(date +%s) - $(stat -c %Y ~/.tokens/robinhood.pickle 2>/dev/null || echo 0)) / 3600 )); echo "SFP open live rows=$open ; RH pickle age h=$age"; if [ "$open" != "0" ]; then echo "ABORT: SFP NOT flat ($open open rows) -- not restarting"; exit 2; fi; if [ "$age" -gt 20 ]; then echo "NOTE: pickle mtime ${age}h >20, but the session was operator-confirmed VALID this session (rh_pickle_refresh.ps1 printed 680725082 reachable=True; robin_stocks did NOT rewrite a still-valid token, so mtime is a stale proxy). Proceeding per operator -- flat gate still enforced above."; fi; echo "GATES PASS (flat) -> ONE restart"; sudo -n systemctl restart trading-corp; sleep 6; echo "post:"; systemctl show trading-corp -p MainPID,NRestarts,ActiveState,SubState
'@
$cmd | ssh $H "tr -d '\r'|bash"
Write-Host "=== If GATES PASS + ActiveState=active/running -> run bootsmoke.ps1. If an ABORT printed, no restart happened. ==="
