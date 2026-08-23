# pk_pm_sdt_reverify.ps1 -- confirm the SDTrading net-verify 1-row delta is LIVE-DATA TIMING, not a logic
# bug: re-backfill SDTrading (idempotent, syncs DB to the current snapshot) then run the from-scratch
# net-verify against that same snapshot -> expect DB == INDEP to the cent. Additive; no restart/sudo/legacy.
# Run: powershell -ep bypass -f .\pk_pm_sdt_reverify.ps1
$ErrorActionPreference = 'Stop'
$enc = New-Object Text.UTF8Encoding($false)
$tf = Join-Path $env:TEMP 'pk_sdtrv_box.sh'
$bash = @'
cd /home/azureuser/trading_corp
OUT=/tmp/pm_sdtrv.txt
{
echo "=== re-backfill SDTrading (idempotent; sync DB to current snapshot) ==="
PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py backfill --only-wallets 0x16bb9951a36fce71e2ef57890b786145e0ba8492 2>&1 | tail -10
echo "=== net-verify vs the just-synced snapshot (from-scratch predicate) ==="
venv/bin/python - <<'PY'
import sqlite3, json, urllib.request
from collections import defaultdict
DATA="https://data-api.polymarket.com"; SDT="0x16bb9951a36fce71e2ef57890b786145e0ba8492"
def http(u):
    r=urllib.request.Request(u, headers={"User-Agent":"nv/1.0"})
    with urllib.request.urlopen(r, timeout=40) as x: return json.loads(x.read().decode())
def f(v):
    try: return float(v) if v is not None else 0.0
    except Exception: return 0.0
raw=[]; off=0
while off<20000:
    p=http("%s/closed-positions?user=%s&limit=50&offset=%d"%(DATA,SDT,off))
    if not p: break
    raw.extend(p)
    if len(p)<50: break
    off+=50
by={}
for r in raw: by[(SDT,str(r.get("conditionId") or ""),int(r.get("outcomeIndex") or 0))]=r
recs=[]
for r in by.values():
    tb=f(r.get("totalBought")); avg=f(r.get("avgPrice")); rp=f(r.get("realizedPnl")); es=(r.get("eventSlug") or "").strip(); cb=tb*avg
    recs.append({"es":es,"tb":tb,"rp":rp,"cb":cb,"cb0":(tb<=0 and rp!=0),"ncb":(cb<=0),"mlb":(r.get("eventSlug") or r.get("slug") or "").lower().startswith("mlb")})
g=defaultdict(list)
for x in recs:
    if x["es"]: g[x["es"]].append(x)
for gg in g.values():
    if any(x["cb0"] for x in gg):
        for x in gg: x["egp"]=True
for x in recs: x["susp"]=x["cb0"] or x.get("egp",False) or x["ncb"]
mlb=[x for x in recs if x["mlb"]]; sc=[x for x in mlb if not x["susp"]]
inet=sum(x["rp"] for x in sc); icost=sum(x["cb"] for x in sc); inx=sum(1 for x in mlb if x["susp"])
c=sqlite3.connect("data/prediction_markets.db"); c.row_factory=sqlite3.Row
d=c.execute("SELECT n_resolved,n_excluded,net_realized_pnl,cost_basis,roi FROM pm_category_stats WHERE wallet=? AND category='mlb'",(SDT,)).fetchone(); c.close()
print("raw=%d mlb=%d scoreable=%d"%(len(raw),len(mlb),len(sc)))
print("  n_resolved   DB=%s INDEP=%s"%(d["n_resolved"],len(sc)))
print("  net_realized DB=%.4f INDEP=%.4f delta=%+.4f"%(d["net_realized_pnl"],inet,d["net_realized_pnl"]-inet))
print("  cost_basis   DB=%.4f INDEP=%.4f delta=%+.4f"%(d["cost_basis"],icost,d["cost_basis"]-icost))
print("  VERDICT net_match=%s cost_match=%s nres_match=%s"%(abs(d["net_realized_pnl"]-inet)<0.01,abs(d["cost_basis"]-icost)<0.01,d["n_resolved"]==len(sc)))
PY
} > "$OUT" 2>&1
echo "RUN_DONE lines=$(wc -l < "$OUT")"
'@
$bash = $bash -replace "`r", ""
[IO.File]::WriteAllText($tf, $bash, $enc)
Write-Host "== SDTrading net-verify re-check (same snapshot) =="
$runMsg = az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Write-Host $runMsg
$lines = 40; $runStr = ($runMsg | Out-String); if ($runStr -match 'lines=(\d+)') { $lines = [int]$Matches[1] }
$per = 30; $nchunks = [math]::Ceiling(($lines + 2) / $per)
for ($n = 0; $n -lt $nchunks; $n++) {
    $a = $n * $per + 1; $b = $n * $per + $per
    [IO.File]::WriteAllText($tf, "sed -n '$a,${b}p' /tmp/pm_sdtrv.txt`n", $enc)
    Write-Host ("---- OUTPUT lines " + $a + "-" + $b + " ----")
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
}
