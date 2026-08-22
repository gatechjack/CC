# pk_ufc_score_stage2_ro.ps1 -- READ-ONLY UFC whale NET scoring (Stage 2, v2 CORRECTED). NO writes.
# FIX1: win/loss from real market resolution (outcomePrices winner vs the outcome the whale bought) ->
#       LOSSES counted. FIX2: paginate /activity to the 5000 cap + flag truncation.
# NET realized = SELL+REDEEM-BUY over RESOLVED UFC decisions (losers included as -BUY).
# Run: powershell -ep bypass -f .\pk_ufc_score_stage2_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
venv/bin/python3 - <<'PY'
import json, urllib.request, re, datetime
def http(url, timeout=30):
    req=urllib.request.Request(url, headers={"User-Agent":"research/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read().decode())
DATA="https://data-api.polymarket.com"; GAMMA="https://gamma-api.polymarket.com"
DATE=re.compile(r"-\d{4}-\d{2}-\d{2}$")
CAND=[
 ("0x5b6331e7ff0831a3fe2ed12004747db1a9c911a4","neutralwave23"),
 ("0xe91171f655be1568e4c63f29663c0028649e3d4e","0xe91171"),
 ("0xbb8ef6381e4a35b4124584cfce1c73137e2508c1","grandculp"),
 ("0x62bac5faa9669d5e8097405a3272152e5d7086fd","black-marduk"),
 ("0xe9076a87c5ed90ef16e6fe6529c943baeca0cff6","suntori"),
 ("0xfd3e6449d0c1e807501dcc17c0d9447201f35a7a","User7429"),
 ("0x6dd6314d1670f9f1ccccbd6746b0bf2f2fa0f5f4","4751346"),
 ("0x6b23c73159b753c96d3c1ed94413f113300c04a3","pauleta23"),
 ("0xf4f88a1e25ff5dfae68326eb62049d6a1b7c5bf1","cosmicsteaks"),
 ("0xb3f32eb604d65ee6bf09e5302c0e1b3dcc603426","chao9152"),
 ("0xc3e550fae1c90b71675f3355e5864c240bea519d","kutsumiakia"),
 ("0x51f7c3d9fa8cc71818aede2db6e4496968ecae1f","FRANK.THE.TANK"),
 ("0x52f454c43b23504d2dc39e034bf19469fd592b15","Kh4mz4t"),
 ("0x43e0f84fe8fb4623a5ff485fe9f7bc0f4b458618","evanng"),
 ("0x1f7105a18d9f36aef7c83e5df210e57cf487b2e4","000why000"),
 ("0xa38a455bbdd4b68486548b7e19da99903f4f821d","takeormake"),
 ("0x7a5d94f83ff0195387eaf9f4463433d03eda54db","mishipolis"),
 ("0x767a7964deeea63dddd0cba6db39503f328d8ac5","MadeiraIsland"),
 ("0x99b1b05948d6e58a51fcd366b7e4b183b198196a","STC14"),
 ("0x8056189d56833ce5b3945dea9149b62c5111b64d","csgod"),
]
def amt(r):
    v=r.get("usdcSize")
    if v is None:
        try: v=float(r.get("size",0) or 0)*float(r.get("price",0) or 0)
        except Exception: v=0.0
    try: return float(v or 0)
    except Exception: return 0.0
def winner_of(m):
    op=m.get("outcomePrices"); outs=m.get("outcomes")
    if isinstance(op,str):
        try: op=json.loads(op)
        except Exception: op=None
    if isinstance(outs,str):
        try: outs=json.loads(outs)
        except Exception: outs=None
    if not op or not outs: return None
    fp=[float(x) for x in op]
    if max(fp) < 0.99: return "VOID"
    return outs[fp.index(max(fp))]
def resolve(cids):
    out={}
    cids=[c for c in cids if c]
    for i in range(0,len(cids),20):
        chunk=cids[i:i+20]
        url="%s/markets?%s&closed=true&limit=100" % (GAMMA, "&".join("condition_ids=%s"%c for c in chunk))
        try: ms=http(url)
        except Exception: ms=[]
        for m in (ms or []):
            cid=m.get("conditionId") or m.get("condition_id")
            if cid: out[cid]=winner_of(m)
    return out

print("wallet            n_res single/fut  W-L-V   win%   NET_usdc     ROI%   avg_buy  last_ufc  trunc")
rows=[]
for wallet,name in CAND:
    acts=[]; trunc=False
    for off in range(0,5000,500):
        try: a=http("%s/activity?user=%s&limit=500&offset=%d"%(DATA,wallet,off))
        except Exception: break
        if not a: break
        acts+=a
        if len(a)<500: break
        if off==4500: trunc=True
    g={}
    for r in acts:
        slug=r.get("slug","") or ""
        if not slug.startswith("ufc-"): continue
        cid=r.get("conditionId") or r.get("condition_id")
        if not cid: continue
        d=g.setdefault(cid,{"slug":slug,"buy":0.0,"sell":0.0,"redeem":0.0,"ts":0,"single":bool(DATE.search(slug)),"obuy":{}})
        typ=(r.get("type","") or "").upper(); side=(r.get("side","") or "").upper(); a_=amt(r)
        if typ=="TRADE" and side=="BUY":
            d["buy"]+=a_; oc=r.get("outcome","") or ""; d["obuy"][oc]=d["obuy"].get(oc,0.0)+a_
        elif typ=="TRADE" and side=="SELL": d["sell"]+=a_
        elif typ=="REDEEM": d["redeem"]+=a_
        d["ts"]=max(d["ts"], int(r.get("timestamp",0) or 0))
    win=resolve(g.keys())
    n=nsingle=nfut=w=l=v=0; net=0.0; buys=0.0; last=0
    for cid,d in g.items():
        wl=win.get(cid)
        if wl is None: continue           # not resolved (open) -> skip
        n+=1; buys+=d["buy"]; net+=d["sell"]+d["redeem"]-d["buy"]; last=max(last,d["ts"])
        if d["single"]: nsingle+=1
        else: nfut+=1
        bet=max(d["obuy"].items(), key=lambda kv: kv[1])[0] if d["obuy"] else ""
        if wl=="VOID": v+=1
        elif bet and bet==wl: w+=1
        else: l+=1
    if n==0:
        print("  %-16s 0     -           -       -      -            -        -        -         %s"%(name, "Y" if trunc else "-")); continue
    winp=(100.0*w/(w+l)) if (w+l) else float("nan")
    roi=(100.0*net/buys) if buys>0 else float("nan")
    avgb=buys/n
    lastd=datetime.datetime.fromtimestamp(last, datetime.timezone.utc).strftime("%Y-%m-%d") if last else "?"
    rows.append((net,name,wallet,n,nsingle,nfut,w,l,v,winp,roi,avgb,lastd,trunc))
    print("  %-16s %-5d %d/%-8d %d-%d-%-2d %5.0f  %+11.2f  %6.1f  %7.0f  %s  %s" % (
        name,n,nsingle,nfut,w,l,v,(winp if winp==winp else -1),net,(roi if roi==roi else -999),avgb,lastd,"Y" if trunc else "-"))

print("\n=== RANKED BY NET UFC REALIZED (losses INCLUDED; win%% = W/(W+L), void excl) ===")
for net,name,wallet,n,nsingle,nfut,w,l,v,winp,roi,avgb,lastd,trunc in sorted(rows,reverse=True):
    fl=[]
    if n<20: fl.append("TINY-N")
    if trunc: fl.append("TRUNCATED")
    if nfut>nsingle: fl.append("FUTURES-SKEW")
    print("  %-16s NET=$%+10.2f ROI=%5.1f%% n=%d(sf %d/fut %d) W-L=%d-%d win%%=%2.0f last=%s %s" % (
        name,net,(roi if roi==roi else -999),n,nsingle,nfut,w,l,(winp if winp==winp else -1),lastd," ".join(fl)))
print("\nNET=SELL+REDEEM-BUY over RESOLVED UFC decisions (losers = -BUY). win%%=W/(W+L) by resolution winner vs")
print("outcome bought. n<20 NOT a verdict. TRUNCATED=hit 5000-activity cap (UFC history may extend further).")
PY
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$enc = New-Object Text.UTF8Encoding($false)
$tf  = Join-Path $env:TEMP 'pk_ufcs_chunk.sh'
$size = 20000; $first = $true
for ($i = 0; $i -lt $b64.Length; $i += $size) {
    $chunk = $b64.Substring($i, [Math]::Min($size, $b64.Length - $i))
    $op = if ($first) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pk_ufcs.b64`n", $enc)
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $first = $false
}
[IO.File]::WriteAllText($tf, "base64 -d /tmp/pk_ufcs.b64 | bash`n", $enc)
Write-Host "== UFC WHALE NET SCORING STAGE 2 v2 CORRECTED (READ-ONLY) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Remove-Item $tf -ErrorAction SilentlyContinue
