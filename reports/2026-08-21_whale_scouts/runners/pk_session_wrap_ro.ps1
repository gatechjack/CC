# pk_session_wrap_ro.ps1 -- READ-ONLY session-wrap state confirm. NO writes.
# live loop armed/halt/PID + live_whales + PCT paper roster (selected+pinned) + geoblock recency.
# Run: powershell -ep bypass -f .\pk_session_wrap_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
echo "MainPID = $(systemctl show -p MainPID --value trading-corp 2>/dev/null)"
venv/bin/python3 - <<'PY'
import os, yaml
try:
    from dotenv import load_dotenv; load_dotenv("/home/azureuser/trading_corp/.env")
except Exception: pass
from trading_corp.persistence.models import StrategyState
from trading_corp.persistence.db import load_agent_state
DB=os.environ.get("TRADING_CORP_DB_URL","sqlite:///data/trading_corp.db")
cfg=(yaml.safe_load(open("config/strategies.yaml")) or {}).get("poly_kalshi_mlb") or {}
auto=bool(cfg.get("auto_execute", False))
print("poly_kalshi_mlb: auto_execute=%s dry_run=%s halted=%s"%(auto,(not auto),StrategyState.from_persistence("poly_kalshi_mlb",db_url=DB).halted))
def names(actor,key):
    r=load_agent_state(actor,key,db_url=DB); v=r[0] if r else None
    if not isinstance(v,list): return (0,[])
    return (len(v),[ (x.get("user_name","") if isinstance(x,dict) else x) for x in v])
nl,lw=names("poly_kalshi_mlb","live_whales")
ns,sw=names("polymarket_copy_trader","selected_whales")
npn,pw=names("polymarket_copy_trader","pinned_whales")
print("LIVE  live_whales (%d): %s"%(nl,lw))
print("PAPER selected_whales (%d): %s"%(ns,sw))
print("PAPER pinned_whales   (%d): %s"%(npn,pw))
PY
echo "-- geoblock recency (confirm still blocked, no action) --"
echo "403_last15min=$(journalctl -u trading-corp --since '15 min ago' --no-pager 2>/dev/null | grep -c 'not currently allowed in Washington')"
journalctl -u trading-corp --no-pager -o short-iso 2>/dev/null | grep 'not currently allowed in Washington' | tail -1 | cut -c1-25 | sed 's/^/last_403=/'
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$enc = New-Object Text.UTF8Encoding($false)
$tf  = Join-Path $env:TEMP 'pk_wrap_chunk.sh'
$size = 20000; $first = $true
for ($i = 0; $i -lt $b64.Length; $i += $size) {
    $chunk = $b64.Substring($i, [Math]::Min($size, $b64.Length - $i))
    $op = if ($first) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pk_wrap.b64`n", $enc)
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $first = $false
}
[IO.File]::WriteAllText($tf, "base64 -d /tmp/pk_wrap.b64 | bash`n", $enc)
Write-Host "== SESSION-WRAP STATE CONFIRM (READ-ONLY) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Remove-Item $tf -ErrorAction SilentlyContinue
