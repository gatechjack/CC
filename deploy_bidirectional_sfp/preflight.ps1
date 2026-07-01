# preflight.ps1 - bidirectional SFP deploy: READ-ONLY gates + drift-gate snapshot.
# Aborts on any red gate. ASCII-only; remote bash streamed via STDIN (CR-stripped).
# Operator paste (one line): powershell -ep bypass -f .\preflight.ps1
$ErrorActionPreference = "Stop"
$H = "azureuser@trading.jacksumner.com"
Write-Host "=== PREFLIGHT (bidirectional SFP) ==="

# 1) LOCAL git parity: main == origin/main (staging must be pushed-parity)
$m = (git rev-parse main); $o = (git rev-parse origin/main)
if ($m -ne $o) { Write-Host "ABORT: main($m) != origin/main($o)"; exit 1 }
Write-Host ("OK  git main==origin  " + $m)

# 2) REMOTE snapshot: touched-file prod md5s (drift-gate base) + detector + RH pickle + SFP flat
$cmd = @'
cd /home/azureuser/trading_corp; echo "== PROD MD5 (drift-gate base) =="; md5sum trading_corp/main.py trading_corp/agents/divisions/bitunix_sfp_observer.py trading_corp/agents/divisions/bitunix_position_reconciler.py trading_corp/web/sfp_cockpit_view.py trading_corp/web/templates/sfp_cockpit/_state_board.html config/strategies.yaml; echo "== DETECTOR (must == 91fd7672) =="; md5sum trading_corp/agents/strategies/bitunix_sfp.py; echo "== RH pickle age hours (>20 => refresh FIRST) =="; echo pickle_age_h=$(( ($(date +%s) - $(stat -c %Y ~/.tokens/robinhood.pickle 2>/dev/null || echo 0)) / 3600 )); echo "== SFP flat: open live rows (must be 0) =="; sqlite3 data/trading_corp.db "SELECT COUNT(*) FROM paper_trade_record WHERE division='bitunix_sfp' AND result IS NULL"; echo "== SFP reconciler last state (want match_count==0 / clean) =="; sudo -n journalctl -u trading-corp --since "2 hours ago" --no-pager 2>/dev/null | grep -iE "reconciler .bitunix_sfp." | tail -1; echo "== engine =="; systemctl show trading-corp -p MainPID,NRestarts,ActiveState
'@
$cmd | ssh $H "tr -d '\r'|bash" | Tee-Object -FilePath .\preflight_prod_snapshot.txt

Write-Host "=== REVIEW (all must hold before apply) ==="
Write-Host " - detector md5 == 91fd7672"
Write-Host " - RH pickle age < 20h  (ELSE: run rh_pickle_refresh.ps1 FIRST -- stale pickle hangs the WHOLE engine on 2FA)"
Write-Host " - SFP open live rows == 0  AND reconciler line = clean/match_count==0  (bitunix_futures may hold independently -- isolated, does NOT block)"
Write-Host " - preflight_prod_snapshot.txt saved = the drift-gate base for apply.ps1"
