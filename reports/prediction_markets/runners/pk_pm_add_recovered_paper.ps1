# pk_pm_add_recovered_paper.ps1 -- ITEM 2: add the TWO wrongly-excluded UFC whales (evanng, MadeiraIsland)
# to the PCT PAPER roster (polymarket_copy_trader/selected_whales + pinned_whales), following the
# pk_add_ufc_paper.ps1 lineage EXACTLY: agent_state only, backup first, read-back confirm, -Reverse supported,
# live loop UNTOUCHED, NO restart. INVARIANT: neither may be on poly_kalshi_mlb/live_whales.
# This writes ONLY the polymarket_copy_trader selected/pinned keys (the Board-authorized agent_state exception);
# it is a targeted key upsert, not broad legacy-DB contact. Delivery = clean @file (no base64 tunnel).
#   DRY (default): preview, NO write.   -Apply: write (+pinned with -Pin), backup first.   -Reverse: restore newest backup.
# Run (preview): powershell -ep bypass -f .\pk_pm_add_recovered_paper.ps1
# Run (apply)  : powershell -ep bypass -f .\pk_pm_add_recovered_paper.ps1 -Apply -Pin
param([switch]$Apply,[switch]$Reverse,[switch]$Pin)
$ErrorActionPreference = 'Stop'
$mode = if ($Reverse) { 'reverse' } elseif ($Apply) { 'apply' } else { 'dry' }
$pinv = if ($Pin) { '1' } else { '0' }
$enc = New-Object Text.UTF8Encoding($false)
$tf  = Join-Path $env:TEMP 'pk_addrec_box.sh'
$bash = @'
cd /home/azureuser/trading_corp
venv/bin/python3 - <<'PY'
import os, json, time, glob
try:
    from dotenv import load_dotenv; load_dotenv("/home/azureuser/trading_corp/.env")
except Exception: pass
from trading_corp.persistence.db import load_agent_state, set_agent_state
MODE=os.environ.get("PK_MODE","dry"); PIN=os.environ.get("PK_PIN","0")=="1"
DB=os.environ.get("TRADING_CORP_DB_URL","sqlite:///data/trading_corp.db")
ACTOR="polymarket_copy_trader"
REC=[
 {"wallet":"0x767a7964deeea63dddd0cba6db39503f328d8ac5","user_name":"MadeiraIsland","category":"UFC","tier":1,"source":"rerank_recovered_2026-08-23","note":"+9.0% cost-ROI CONTESTED, 1% two-sided (cleanest UFC)"},
 {"wallet":"0x43e0f84fe8fb4623a5ff485fe9f7bc0f4b458618","user_name":"evanng","category":"UFC","tier":1,"source":"rerank_recovered_2026-08-23","note":"+24.0% cost-ROI CONTESTED (scout -13.7k SIGN FLIP; closes 13A(a)); caveat 41% two-sided"},
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
    for w in REC:
        if lw(w) not in have: out.append(w); have.add(lw(w)); added.append(w["user_name"])
    return out, added
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
clash=[w["user_name"] for w in REC if lw(w) in live]
print("current selected_whales:", len(cur_sel), " pinned_whales:", len(cur_pin), " live_whales:", len(live))
print("INVARIANT live-clash:", clash if clash else "NONE (live INTERSECT recovered == empty, OK)")
if clash:
    print("ABORT_INVARIANT: %s already LIVE-rostered" % clash); raise SystemExit(3)
new_sel,add_sel=merge(cur_sel)
new_pin,add_pin=merge(cur_pin) if PIN else (cur_pin,[])
print("\nselected_whales: %d -> %d (adding: %s)" % (len(cur_sel), len(new_sel), add_sel or "none/all-present"))
if PIN: print("pinned_whales:   %d -> %d (adding: %s)" % (len(cur_pin), len(new_pin), add_pin or "none/all-present"))
else:   print("pinned_whales:   UNCHANGED (%d) -- use -Pin to persist across weekly refresh" % len(cur_pin))
print("resulting selected_whales wallets:")
for w in new_sel: print("   ", lw(w), (w.get("user_name","") if isinstance(w,dict) else ""))

if MODE=="dry":
    print("\nDRY-RUN -- no write. Re-run with -Apply -Pin.")
    raise SystemExit(0)

ts=time.strftime("%Y%m%d_%H%M%S", time.gmtime())
bak="/home/azureuser/pk_paper_roster_bak_%s.json" % ts
json.dump({"selected":cur_sel,"pinned":cur_pin}, open(bak,"w"))
print("\nBACKUP written:", bak)
set_agent_state(ACTOR,"selected_whales", new_sel, db_url=DB)
if PIN: set_agent_state(ACTOR,"pinned_whales", new_pin, db_url=DB)
rb_sel=getlist("selected_whales"); rb_pin=getlist("pinned_whales"); live2=livewallets()
still=[w["user_name"] for w in REC if lw(w) in live2]
print("READBACK selected_whales:", len(rb_sel), " pinned_whales:", len(rb_pin))
ok_sel=all(any(lw(x)==lw(w) for x in rb_sel) for w in REC)
ok_pin=(all(any(lw(x)==lw(w) for x in rb_pin) for w in REC) if PIN else True)
print("both recovered present in selected_whales:", ok_sel, " in pinned_whales:", ok_pin)
print("post-write invariant live-clash:", still if still else "NONE (OK; live INTERSECT paper == empty)")
print("APPLIED_OK" if (ok_sel and ok_pin and not still) else "APPLIED_WITH_WARNING")
PY
'@
$body = "export PK_MODE=$mode PK_PIN=$pinv`n" + $bash
$body = $body -replace "`r", ""
[IO.File]::WriteAllText($tf, $body, $enc)
Write-Host "== ADD RECOVERED UFC WHALES TO PCT PAPER (mode=$mode pin=$pinv) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
