# pk_pm_step5.ps1 -- READ-ONLY Step 5: report + full acceptance evidence. Runs the SDTrading MLB net-verify
# (from-scratch predicate, NOT importing ingest), the ranked scoreboard (both routines + JSON parse),
# category coverage vs the amended bar, CONTAMINATED pairs, and clip saturation on the corrected 12-wallet
# data. No writes. All box ops inside this file (run via -f). Run: powershell -ep bypass -f .\pk_pm_step5.ps1
$ErrorActionPreference = 'Stop'
$enc = New-Object Text.UTF8Encoding($false)
$tf = Join-Path $env:TEMP 'pk_step5_box.sh'
$bash = @'
cd /home/azureuser/trading_corp
OUT=/tmp/pm_step5.txt
{
echo "=== PID before ==="
echo "MainPID=$(systemctl show trading-corp.service -p MainPID --value)"
echo "=== S12 NET-VERIFY: SDTrading MLB (from-scratch predicate; NOT importing ingest) ==="
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
rows=list(by.values()); recs=[]
for r in rows:
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
inet=sum(x["rp"] for x in sc); icost=sum(x["cb"] for x in sc); itb=sum(x["tb"] for x in sc)
ifull=sum(x["rp"] for x in mlb); inx=sum(1 for x in mlb if x["susp"])
iroi=(inet/icost) if icost>0 else None; iroin=(inet/itb) if itb>0 else None
c=sqlite3.connect("data/prediction_markets.db"); c.row_factory=sqlite3.Row
d=c.execute("SELECT n_resolved,n_excluded,net_realized_pnl,cost_basis,total_bought,roi,roi_notional FROM pm_category_stats WHERE wallet=? AND category='mlb'",(SDT,)).fetchone(); c.close()
print("raw=%d PKs=%d mlb=%d scoreable=%d"%(len(raw),len(rows),len(mlb),len(sc)))
def L(n,a,b):
    dd="%+.4f"%(a-b) if isinstance(a,(int,float)) and isinstance(b,(int,float)) else "n/a"
    print("  %-20s DB=%-15s INDEP=%-15s delta=%s"%(n,a,b,dd))
if d is None:
    print("  !! no DB row for SDTrading/mlb")
else:
    L("net_realized(score)",round(d["net_realized_pnl"],4),round(inet,4))
    L("cost_basis(score)",round(d["cost_basis"],4),round(icost,4))
    L("n_resolved",d["n_resolved"],len(sc)); L("n_excluded",d["n_excluded"],inx)
    L("roi(cost)",round(d["roi"],6) if d["roi"] is not None else None, round(iroi,6) if iroi is not None else None)
    L("roi_notional",round(d["roi_notional"],6) if d["roi_notional"] is not None else None, round(iroin,6) if iroin is not None else None)
    print("  independent FULL net (all mlb rows):", round(ifull,2))
    print("  VERDICT net_match=%s cost_match=%s nexcl_match=%s"%(abs(d["net_realized_pnl"]-inet)<0.01,abs(d["cost_basis"]-icost)<0.01,d["n_excluded"]==inx))
PY
echo "=== SCOREBOARD report --routine net_roi --min-resolved 10 ==="
PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py report --min-resolved 10 --routine net_roi 2>&1 | head -45
echo "=== SCOREBOARD report --routine recency_weighted (top 12) ==="
PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py report --min-resolved 10 --routine recency_weighted 2>&1 | head -16
echo "=== report --format json parses? ==="
PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_cli.py report --format json 2>&1 | venv/bin/python -c "import sys,json; d=json.load(sys.stdin); print('JSON OK rows=%d'%len(d))"
echo "=== CATEGORY COVERAGE (amended bar; live=MLB/UFC/NBA/Fed; out-of-scope-unknown separate) ==="
venv/bin/python - <<'PY'
import sqlite3
c=sqlite3.connect("data/prediction_markets.db"); c.row_factory=sqlite3.Row
LIVE={"mlb","ufc","nba","fed"}
tot=c.execute("SELECT COUNT(1) FROM pm_closed_position").fetchone()[0]
by={r["c"]:r["n"] for r in c.execute("SELECT category c, COUNT(1) n FROM pm_closed_position GROUP BY 1")}
unk=by.get("unknown",0); live=sum(v for k,v in by.items() if k in LIVE)
print("total rows=%d | in 4 LIVE cats=%d | unknown(out-of-scope)=%d (%.1f%%)"%(tot,live,unk,100.0*unk/tot))
mis=c.execute("SELECT COUNT(1) FROM pm_closed_position WHERE category='unknown' AND (lower(event_slug) LIKE 'mlb-%' OR lower(event_slug) LIKE 'ufc-%' OR lower(event_slug) LIKE 'nba-%' OR lower(event_slug) LIKE 'fed-%')").fetchone()[0]
print("in-scope rows mis-filed as unknown after repair (MUST be 0):", mis)
print("category counts:", dict(sorted(by.items(), key=lambda kv:-kv[1])))
c.close()
PY
echo "=== CONTAMINATED (wallet,category) pairs -- $-weighted data_quality (carry the caveat) ==="
venv/bin/python - <<'PY'
import sqlite3
c=sqlite3.connect("data/prediction_markets.db"); c.row_factory=sqlite3.Row
n=0
for r in c.execute("SELECT wallet,category,n_resolved nr,n_excluded nx,ROUND(dq_count_pct*100,1) cp,ROUND(dq_dollar_pct*100,1) dp FROM pm_category_stats WHERE data_quality='contaminated' ORDER BY dp DESC"):
    print("   %s %-7s nres=%s nexc=%s count%%=%s $%%=%s"%(r["wallet"][:12],r["category"],r["nr"],r["nx"],r["cp"],r["dp"])); n+=1
print("contaminated pairs:", n)
c.close()
PY
echo "=== CLIP SATURATION on corrected data (scored: complete wallets, n_resolved>=10) ==="
venv/bin/python - <<'PY'
import sqlite3
c=sqlite3.connect("data/prediction_markets.db"); c.row_factory=sqlite3.Row
xs=sorted(r["roi"] for r in c.execute("SELECT cs.roi FROM pm_category_stats cs JOIN pm_whale w ON cs.wallet=w.wallet WHERE w.backfill_complete=1 AND cs.n_resolved>=10 AND cs.roi IS NOT NULL"))
ceil=sum(1 for x in xs if x>=2.0); flr=sum(1 for x in xs if x<=-0.5)
print("scored pairs:", len(xs))
if xs: print("cost_roi min=%.1f%% median=%.1f%% max=%.1f%%"%(min(xs)*100, xs[len(xs)//2]*100, max(xs)*100))
print("pinned CEILING(>=+200%%):", ceil, "| pinned FLOOR(<=-50%%):", flr, "-> %s"%("SATURATION" if (ceil+flr)>0 else "NEGLIGIBLE"))
c.close()
PY
echo "=== PID after ==="
echo "MainPID=$(systemctl show trading-corp.service -p MainPID --value)"
} > "$OUT" 2>&1
echo "RUN_DONE lines=$(wc -l < "$OUT")"
'@
$bash = $bash -replace "`r", ""
[IO.File]::WriteAllText($tf, $bash, $enc)
Write-Host "== PM P1 STEP 5 -- REPORT + ACCEPTANCE EVIDENCE =="
$runMsg = az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Write-Host $runMsg
$lines = 120; $runStr = ($runMsg | Out-String); if ($runStr -match 'lines=(\d+)') { $lines = [int]$Matches[1] }
$per = 30; $nchunks = [math]::Ceiling(($lines + 2) / $per)
for ($n = 0; $n -lt $nchunks; $n++) {
    $a = $n * $per + 1; $b = $n * $per + $per
    [IO.File]::WriteAllText($tf, "sed -n '$a,${b}p' /tmp/pm_step5.txt`n", $enc)
    Write-Host ("---- OUTPUT lines " + $a + "-" + $b + " ----")
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
}
