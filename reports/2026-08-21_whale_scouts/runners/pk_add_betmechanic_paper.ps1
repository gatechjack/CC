# pk_add_betmechanic_paper.ps1 -- add BetMechanic (NBA scout) to the PCT PAPER roster
# (polymarket_copy_trader/selected_whales [+pinned_whales]). Paper-only: does NOT touch
# poly_kalshi_mlb/live_whales, does NOT touch the live loop, no restart.
#   DRY (default): preview. -Apply: write (+pinned if -Pin), backup+read-back. -Reverse: restore newest backup.
# Run: powershell -ep bypass -f .\pk_add_betmechanic_paper.ps1 -Apply -Pin
param([switch]$Apply,[switch]$Reverse,[switch]$Pin)
$ErrorActionPreference = 'Stop'
$mode = if ($Reverse) { 'reverse' } elseif ($Apply) { 'apply' } else { 'dry' }
$pinv = if ($Pin) { '1' } else { '0' }
$body = @'
cd /home/azureuser/trading_corp
venv/bin/python3 - <<'PY'
import os, json, time
try:
    from dotenv import load_dotenv; load_dotenv("/home/azureuser/trading_corp/.env")
except Exception: pass
from trading_corp.persistence.db import load_agent_state, set_agent_state
MODE=os.environ.get("PK_MODE","dry"); PIN=os.environ.get("PK_PIN","0")=="1"
DB=os.environ.get("TRADING_CORP_DB_URL","sqlite:///data/trading_corp.db")
ACTOR="polymarket_copy_trader"
ADD=[
 {"wallet":"0xa6a856a8c8a7f14fd9be6ae11c367c7cbb755009","user_name":"BetMechanic","category":"NBA","tier":1,"source":"nba_scout_2026-08-21","note":"+11.1pctROI +94k n71-reg TRUNCATED/partial cross-sport; observe forward"},
]
def lw(v): return str((v.get("wallet") or v.get("proxy_wallet") or "") if isinstance(v,dict) else v).lower()
def getlist(key):
    r=load_agent_state(ACTOR,key,db_url=DB); v=r[0] if r else None
    return v if isinstance(v,list) else []
def livewallets():
    r=load_agent_state("poly_kalshi_mlb","live_whales",db_url=DB); v=r[0] if r else []
    return set(lw(x) for x in v) if isinstance(v,list) else set()
def merge(cur):
    have=set(lw(x) for x in cur); out=list(cur); added=[]
    for w in ADD:
        if lw(w) not in have: out.append(w); have.add(lw(w)); added.append(w["user_name"])
    return out, added
import glob
BAKGLOB="/home/azureuser/pk_paper_roster_bak_*.json"
if MODE=="reverse":
    baks=sorted(glob.glob(BAKGLOB))
    if not baks: print("NO_BACKUP_FOUND"); raise SystemExit(2)
    b=json.load(open(baks[-1])); print("restoring from", baks[-1])
    set_agent_state(ACTOR,"selected_whales", b.get("selected",[]), db_url=DB)
    if "pinned" in b: set_agent_state(ACTOR,"pinned_whales", b.get("pinned",[]), db_url=DB)
    print("REVERSED. selected=%d pinned=%d" % (len(getlist("selected_whales")), len(getlist("pinned_whales"))))
    raise SystemExit(0)
cur_sel=getlist("selected_whales"); cur_pin=getlist("pinned_whales"); live=livewallets()
clash=[w["user_name"] for w in ADD if lw(w) in live]
print("current selected_whales:", len(cur_sel), " pinned_whales:", len(cur_pin), " live_whales:", len(live))
print("current paper wallets:", [ (x.get("user_name","") if isinstance(x,dict) else x) for x in cur_sel])
print("INVARIANT live-clash:", clash if clash else "NONE (live INTERSECT betmechanic == empty, OK)")
if clash:
    print("ABORT_INVARIANT: %s already LIVE-rostered" % clash); raise SystemExit(3)
new_sel,add_sel=merge(cur_sel)
new_pin,add_pin=merge(cur_pin) if PIN else (cur_pin,[])
print("\nselected_whales: %d -> %d (adding: %s)" % (len(cur_sel), len(new_sel), add_sel or "none/already-present"))
if PIN: print("pinned_whales:   %d -> %d (adding: %s)" % (len(cur_pin), len(new_pin), add_pin or "none/already-present"))
else:   print("pinned_whales:   UNCHANGED (%d)" % len(cur_pin))
print("resulting selected_whales:")
for w in new_sel: print("   ", lw(w), (w.get("user_name","") if isinstance(w,dict) else ""), (w.get("category","") if isinstance(w,dict) else ""))
if MODE=="dry":
    print("\nDRY-RUN -- no write. Re-run with -Apply -Pin to commit.")
    raise SystemExit(0)
ts=time.strftime("%Y%m%d_%H%M%S", time.gmtime())
bak="/home/azureuser/pk_paper_roster_bak_%s.json" % ts
json.dump({"selected":cur_sel,"pinned":cur_pin}, open(bak,"w"))
print("\nBACKUP written:", bak)
set_agent_state(ACTOR,"selected_whales", new_sel, db_url=DB)
if PIN: set_agent_state(ACTOR,"pinned_whales", new_pin, db_url=DB)
rb_sel=getlist("selected_whales"); rb_pin=getlist("pinned_whales"); live2=livewallets()
still=[w["user_name"] for w in ADD if lw(w) in live2]
print("READBACK selected_whales:", len(rb_sel), " pinned_whales:", len(rb_pin))
ok=all(any(lw(x)==lw(w) for x in rb_sel) for w in ADD)
print("BetMechanic present in selected_whales:", ok)
print("post-write invariant live-clash:", still if still else "NONE (OK)")
print("live_whales untouched count:", len(livewallets()))
print("APPLIED_OK" if (ok and not still) else "APPLIED_WITH_WARNING")
PY
'@
$body = "export PK_MODE=$mode PK_PIN=$pinv" + "`n" + $body
$body = $body -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($body))
$enc = New-Object Text.UTF8Encoding($false)
$tf  = Join-Path $env:TEMP 'pk_addbm_chunk.sh'
$size = 20000; $first = $true
for ($i = 0; $i -lt $b64.Length; $i += $size) {
    $chunk = $b64.Substring($i, [Math]::Min($size, $b64.Length - $i))
    $op = if ($first) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pk_addbm.b64`n", $enc)
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $first = $false
}
[IO.File]::WriteAllText($tf, "base64 -d /tmp/pk_addbm.b64 | bash`n", $enc)
Write-Host "== ADD BetMechanic TO PCT PAPER ROSTER (mode=$mode pin=$pinv) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Remove-Item $tf -ErrorAction SilentlyContinue
