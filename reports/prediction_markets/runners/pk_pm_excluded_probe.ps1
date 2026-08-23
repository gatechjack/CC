# pk_pm_excluded_probe.ps1 -- READ-ONLY. Re-pull the SCOUT-EXCLUDED whales from /closed-positions and
# apply the CORRECTED predicate (S3A clause-b + no-cost-basis + event-group propagation; clause-a as a
# non-excluding flag) + COST-based ROI, to test whether any were wrongly excluded on the broken /activity
# scout method. Hits the PUBLIC data-api only; writes NOTHING (no DB, no roster). Same read-only pattern
# as the net-verify. Run: powershell -ep bypass -f .\pk_pm_excluded_probe.ps1
$ErrorActionPreference = 'Stop'
$enc = New-Object Text.UTF8Encoding($false)
$tf = Join-Path $env:TEMP 'pk_exclprobe_box.sh'
$bash = @'
cd /home/azureuser/trading_corp
OUT=/tmp/pm_exclprobe.txt
venv/bin/python - > "$OUT" 2>&1 <<'PY'
import urllib.request, json, time
from collections import defaultdict
DATA="https://data-api.polymarket.com"
# scout-excluded whales (full addrs from scout runners), each with the category it was scouted/rostered for
WHALES=[
 ("evanng",      "0x43e0f84fe8fb4623a5ff485fe9f7bc0f4b458618","ufc"),
 ("csgod",       "0x8056189d56833ce5b3945dea9149b62c5111b64d","ufc"),
 ("MadeiraIsland","0x767a7964deeea63dddd0cba6db39503f328d8ac5","ufc"),
 ("SadMan",      "0xe9b8005c55e8ef2c311fbbd427521c981828cc82","nfl"),
 ("peter003",    "0xf508c2ce8cfee5926d42c2855d659b8a089f265c","nba"),
 ("d1k21",       "0x71ed0bc95433cdf1be29f43219725fce9addd9eb","fed"),
]
PREFIX={"mlb":"mlb","nba":"nba","nfl":"nfl","nhl":"nhl","ufc":"ufc","cs2":"cs2","atp":"atp","wta":"wta",
 "cbb":"cbb","fifwc":"fifwc","epl":"epl","ucl":"ucl","wnba":"wnba","nascar":"nascar",
 "fed-decision":"fed","fed-interest-rates":"fed","fed-rate":"fed","fed":"fed"}
PREFS=sorted(PREFIX,key=len,reverse=True)
def cat_of(es,sl):
    for cand in (es,sl):
        s=(cand or "").strip().lower()
        if not s: continue
        for p in PREFS:
            if s==p or s.startswith(p+"-"): return PREFIX[p]
    return "unknown"
def f(v):
    try: return float(v) if v is not None else 0.0
    except Exception: return 0.0
def http(u):
    for attempt in range(5):
        try:
            r=urllib.request.Request(u, headers={"User-Agent":"exclprobe/1.0"})
            with urllib.request.urlopen(r, timeout=45) as x: return json.loads(x.read().decode())
        except urllib.error.HTTPError as e:
            if e.code==429: time.sleep(2*(attempt+1)); continue
            raise
    raise RuntimeError("429-exhausted")
def pull(w):
    raw=[]; off=0
    while off<30000:
        p=http("%s/closed-positions?user=%s&limit=50&offset=%d"%(DATA,w,off))
        if not p: break
        raw.extend(p)
        if len(p)<50: break
        off+=50; time.sleep(0.15)
    return raw
print("EXCLUDED-WHALE READ-ONLY RE-PULL (corrected predicate + cost-ROI). PASS criterion for 'wrongly")
print("excluded' = positive net AND positive cost-ROI in the rostered category, contested (not chalk).")
print("="*100)
for name,w,rcat in WHALES:
    try: raw=pull(w)
    except Exception as e:
        print("\n### %-13s rostered=%s  PULL FAILED: %s"%(name,rcat,e)); continue
    # dedupe on (condition_id, outcome_index) -- the PK
    by={}
    for r in raw: by[(str(r.get("conditionId") or ""),int(r.get("outcomeIndex") or 0))]=r
    recs=[]
    for r in by.values():
        tb=f(r.get("totalBought")); avg=f(r.get("avgPrice")); rp=f(r.get("realizedPnl"))
        cp=f(r.get("curPrice")); es=(r.get("eventSlug") or "").strip(); sl=(r.get("slug") or "").strip()
        cb=tb*avg
        recs.append({"cid":str(r.get("conditionId") or ""),"oi":int(r.get("outcomeIndex") or 0),
            "es":es,"cat":cat_of(es,sl),"tb":tb,"avg":avg,"rp":rp,"cb":cb,"cp":cp,
            "won":(1 if cp>=0.9 else 0),
            "cb_b":(tb<=0 and rp!=0),  # clause (b) zero-cost/nonzero
            "ncb":(cb<=0)})            # no-cost-basis (Ruling A)
    # event-group propagation: any clause-b OR no-cost-basis leg taints its (event_slug) group
    g=defaultdict(list)
    for x in recs:
        if x["es"]: g[x["es"]].append(x)
    for gg in g.values():
        if any(x["cb_b"] for x in gg):
            for x in gg: x["egp"]=True
    for x in recs: x["susp"]=x["cb_b"] or x.get("egp",False) or x["ncb"]
    # all-category net (context, scoreable)
    allnet=sum(x["rp"] for x in recs if not x["susp"])
    # rostered-category scoreable slice
    rc=[x for x in recs if x["cat"]==rcat and not x["susp"]]
    wins=sum(1 for x in rc if x["won"]); losses=sum(1 for x in rc if not x["won"])
    net=sum(x["rp"] for x in rc); cost=sum(x["cb"] for x in rc); tbsum=sum(x["tb"] for x in rc)
    awp=([x["avg"] for x in rc if x["won"]])
    awp=sum(awp)/len(awp) if awp else None
    n=len(rc)
    # two-sided in rostered cat
    per=defaultdict(set)
    for x in recs:
        if x["cat"]==rcat: per[x["cid"]].add(x["oi"])
    nm=len(per); ts=sum(1 for k,v in per.items() if len(v)>1)
    roiC=(net/cost) if cost>0 else None
    roiN=(net/tbsum) if tbsum>0 else None
    flag="CHALK" if (awp is not None and awp>=0.85) else ("CONTESTED" if (awp is not None and awp<0.70) else "mid")
    print("\n### %-13s rostered=%s  raw=%d dedup=%d  ALLcat_scoreable_net=%+.0f"%(name,rcat,len(raw),len(by),allnet))
    print("   %s: n=%d W/L=%d/%d win%%=%s net=%+.0f cost=%.0f roiC=%s roiN=%s avgWinPx=%s [%s]  two-sided=%d/%d(%.0f%%)  %s"%(
        rcat,n,wins,losses,("%.0f"%(100.0*wins/(wins+losses)) if (wins+losses) else "-"),net,cost,
        ("%+.1f%%"%(100*roiC) if roiC is not None else "-"),
        ("%+.1f%%"%(100*roiN) if roiN is not None else "-"),
        ("%.2f"%awp if awp is not None else "-"),flag,ts,nm,(100.0*ts/nm if nm else 0),
        ("RANKABLE(n>=10)" if n>=10 else "UNVERIFIABLE(n<10)")))
print("\n"+"="*100+"\nDONE")
PY
echo "EXCLPROBE_DONE lines=$(wc -l < "$OUT")"
cat "$OUT"
'@
$bash = $bash -replace "`r", ""
[IO.File]::WriteAllText($tf, $bash, $enc)
Write-Host "== EXCLUDED-WHALE read-only re-pull (corrected predicate) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
