# pk_pct_scope_ro.ps1 -- READ-ONLY Part-A scope: PCT paper roster (selected_whales) state, live roster
# (poly_kalshi_mlb/live_whales) invariant check for the 5 UFC wallets, PCT active-papering check,
# and poly_kalshi roster-config confirm. NO writes. Run: powershell -ep bypass -f .\pk_pct_scope_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
venv/bin/python3 - <<'PY'
import os, yaml
from datetime import datetime, timezone, timedelta
try:
    from dotenv import load_dotenv; load_dotenv("/home/azureuser/trading_corp/.env")
except Exception: pass
from trading_corp.persistence.db import load_agent_state
from trading_corp.persistence import db
DB=os.environ.get("TRADING_CORP_DB_URL","sqlite:///data/trading_corp.db")
UFC=[("0x52f454c43b23504d2dc39e034bf19469fd592b15","Kh4mz4t"),
     ("0x99b1b05948d6e58a51fcd366b7e4b183b198196a","STC14"),
     ("0x1f7105a18d9f36aef7c83e5df210e57cf487b2e4","000why000"),
     ("0x6dd6314d1670f9f1ccccbd6746b0bf2f2fa0f5f4","4751346"),
     ("0xc3e550fae1c90b71675f3355e5864c240bea519d","kutsumiakia")]
def wl(v):
    if isinstance(v,dict): return str(v.get("wallet") or v.get("proxy_wallet") or "").lower()
    return str(v).lower()

print("=== PCT paper roster: polymarket_copy_trader / selected_whales ===")
rec=load_agent_state("polymarket_copy_trader","selected_whales",db_url=DB)
sel=rec[0] if rec else None
print("type:", type(sel).__name__, " count:", (len(sel) if isinstance(sel,list) else "N/A"))
sel_wallets=set()
if isinstance(sel,list):
    for v in sel:
        w=wl(v); sel_wallets.add(w)
        nm=v.get("user_name","") if isinstance(v,dict) else ""
        print("   PAPER  %s  %s  (dict=%s)" % (w, nm, isinstance(v,dict)))

print("\n=== LIVE roster: poly_kalshi_mlb / live_whales ===")
lrec=load_agent_state("poly_kalshi_mlb","live_whales",db_url=DB)
live=lrec[0] if lrec else None
live_wallets=set()
if isinstance(live,list):
    for v in live:
        w=wl(v); live_wallets.add(w); print("   LIVE   %s  %s" % (w, v.get("user_name","") if isinstance(v,dict) else ""))

print("\n=== INVARIANT: are the 5 UFC wallets already in LIVE or PAPER? ===")
for w,nm in UFC:
    lo=w.lower()
    print("   %-12s %s  in_live=%s  in_paper=%s" % (nm, w, lo in live_wallets, lo in sel_wallets))

print("\n=== PCT actively papering? would_have_placed rows for polymarket_copy_trader ===")
cut=(datetime.now(timezone.utc)-timedelta(days=7)).isoformat()
with db.connect(DB) as c:
    n=c.execute("select count(*) from audit_event where actor='polymarket_copy_trader' and kind='would_have_placed' and ts>=?", (cut,)).fetchone()[0]
    print("   would_have_placed (last 7d):", n)
    r=c.execute("select ts from audit_event where actor='polymarket_copy_trader' and kind='would_have_placed' order by ts desc limit 1").fetchone()
    print("   most recent paper row:", (r[0] if r else "none ever"))
    cs=c.execute("select count(*) from audit_event where actor='polymarket_copy_trader' and kind='polymarket_copy_cold_start' and ts>=?", (cut,)).fetchone()[0]
    print("   cold_start rows (last 7d):", cs)

print("\n=== poly_kalshi_mlb roster config (confirm it reads live_whales NOT selected_whales) ===")
cfg=(yaml.safe_load(open("config/strategies.yaml")) or {}).get("poly_kalshi_mlb") or {}
print("   roster_actor:", cfg.get("roster_actor"), " roster_key:", cfg.get("roster_key"))
pcfg=(yaml.safe_load(open("config/strategies.yaml")) or {}).get("polymarket_copy_trader") or {}
print("   PCT enabled:", pcfg.get("enabled"), " auto_execute:", pcfg.get("auto_execute"), " autopause_mode:", pcfg.get("autopause_mode"))
PY
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$enc = New-Object Text.UTF8Encoding($false)
$tf  = Join-Path $env:TEMP 'pk_pct_chunk.sh'
$size = 20000; $first = $true
for ($i = 0; $i -lt $b64.Length; $i += $size) {
    $chunk = $b64.Substring($i, [Math]::Min($size, $b64.Length - $i))
    $op = if ($first) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pk_pct.b64`n", $enc)
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $first = $false
}
[IO.File]::WriteAllText($tf, "base64 -d /tmp/pk_pct.b64 | bash`n", $enc)
Write-Host "== PCT PAPER-FARM SCOPE (READ-ONLY) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Remove-Item $tf -ErrorAction SilentlyContinue
