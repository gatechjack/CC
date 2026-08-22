# pk_nba_score_stage2_ro.ps1 -- READ-ONLY NBA whale NET scoring (Stage 2). NO writes.
# NET = SELL+REDEEM-BUY over RESOLVED nba single-game decisions (losers = -BUY). Win/loss from real
# outcomePrices winner vs outcome bought. Ranks by NET ROI (NOT win% -- NBA chalk ~68%+). Flags
# summer/tiny-n/truncated. Run: powershell -ep bypass -f .\pk_nba_score_stage2_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
venv/bin/python3 - <<'PY'
import json, urllib.request, re, datetime
def http(url, timeout=30):
    req=urllib.request.Request(url, headers={"User-Agent":"research/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read().decode())
DATA="https://data-api.polymarket.com"; GAMMA="https://gamma-api.polymarket.com"
GAME=re.compile(r"^nba-[a-z0-9]+-[a-z0-9]+-\d{4}-\d{2}-\d{2}$")
def is_summer(d): return d[5:7] in ("07","08","09","10")
CAND=[
 ("0xd9e0aaca471f489be338fd0f91a26e8669a805f2","0xD9E0"),
 ("0xa6a856a8c8a7f14fd9be6ae11c367c7cbb755009","BetMechanic"),
 ("0xb786b8b6335e77dfad19928313e97753039cb18d","0xb786"),
 ("0xf0b0ef1d6320c6be896b4c9c54dd74407e7f8cab","daroghi"),
 ("0xb13d096b2e90411d0e5f42891d2e73ef4cea2e90","0xb13d"),
 ("0x325f64edd42ac179213e12bc2a8c4f00764dd8ac","YAY-409"),
 ("0xf508c2ce8cfee5926d42c2855d659b8a089f265c","peter003"),
 ("0xee00ba338c59557141789b127927a55f5cc5cea1","S-Works"),
 ("0xfba6af103a629a538664136a418300146d2a375c","0xFBA6"),
 ("0xe40172522c7c64afa2d052ddae6c92cd0f417b88","BoomLaLa"),
 ("0xd72753dae1cb01011c1e69af78ee1f9494bbecce","funnyday"),
 ("0x727bfdcb0ae1a00eca35c4d0a9b85f4e42d4d1a2","pmkt1939"),
 ("0xae363845352952cdea784f581e9ee99a219c8fb5","0xae36"),
 ("0x578d665b6dd21abf12a393579699e35a2686fbb3","025d"),
 ("0xd9a0cdd2f18d42f7cf0540d38036c597925288ee","sadge-387"),
 ("0x2ce7d1fb37ee9d5779e3c4759944180f2bdefd4a","tpu-634"),
 ("0x9a753d12065a8143ac69ea1732f67daab67b4347","VolodyaPGG"),
 ("0x2aa22a2d9379e9adb7cb0d1d760763f697ab4d22","greenreaper"),
 ("0x55307e57ee66d15af36bc0894bbaca65604dd38d","go4it"),
 ("0x11d71db4ceb6e3b246af1443c28b910a45a652e1","g0ated"),
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
    if max(fp)<0.99: return "VOID"
    return outs[fp.index(max(fp))]
def resolve(cids):
    out={}; cids=[c for c in cids if c]
    for i in range(0,len(cids),20):
        ch=cids[i:i+20]
        url="%s/markets?%s&closed=true&limit=100"%(GAMMA,"&".join("condition_ids=%s"%c for c in ch))
        try: ms=http(url)
        except Exception: ms=[]
        for m in (ms or []):
            cid=m.get("conditionId") or m.get("condition_id")
            if cid: out[cid]=winner_of(m)
    return out
print("wallet         n   sum/reg  W-L-V  win%   NET_usdc      ROI%    avg_buy last_nba  trunc")
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
        if not GAME.match(slug): continue
        cid=r.get("conditionId") or r.get("condition_id")
        if not cid: continue
        d=g.setdefault(cid,{"slug":slug,"buy":0.0,"sell":0.0,"redeem":0.0,"ts":0,"obuy":{},"sum":is_summer(slug[-10:])})
        typ=(r.get("type","") or "").upper(); side=(r.get("side","") or "").upper(); a_=amt(r)
        if typ=="TRADE" and side=="BUY":
            d["buy"]+=a_; oc=r.get("outcome","") or ""; d["obuy"][oc]=d["obuy"].get(oc,0.0)+a_
        elif typ=="TRADE" and side=="SELL": d["sell"]+=a_
        elif typ=="REDEEM": d["redeem"]+=a_
        d["ts"]=max(d["ts"], int(r.get("timestamp",0) or 0))
    win=resolve(g.keys())
    n=nsum=nreg=w=l=v=0; net=0.0; buys=0.0; last=0
    for cid,d in g.items():
        wl=win.get(cid)
        if wl is None: continue
        n+=1; buys+=d["buy"]; net+=d["sell"]+d["redeem"]-d["buy"]; last=max(last,d["ts"])
        if d["sum"]: nsum+=1
        else: nreg+=1
        bet=max(d["obuy"].items(), key=lambda kv: kv[1])[0] if d["obuy"] else ""
        if wl=="VOID": v+=1
        elif bet and bet==wl: w+=1
        else: l+=1
    if n==0:
        print("  %-13s 0   -        -       -     -             -       -       -         %s"%(name,"Y" if trunc else "-")); continue
    winp=(100.0*w/(w+l)) if (w+l) else float("nan")
    roi=(100.0*net/buys) if buys>0 else float("nan")
    avgb=buys/n
    lastd=datetime.datetime.fromtimestamp(last, datetime.timezone.utc).strftime("%Y-%m-%d") if last else "?"
    rows.append((roi if roi==roi else -1e9, net, name, wallet, n, nsum, nreg, w, l, v, winp, roi, avgb, lastd, trunc))
    print("  %-13s %-3d %d/%-6d %d-%d-%-2d %5.0f %+12.2f %7.1f %7.0f %s  %s"%(
        name,n,nsum,nreg,w,l,v,(winp if winp==winp else -1),net,(roi if roi==roi else -999),avgb,lastd,"Y" if trunc else "-"))
print("\n=== RANKED BY NET ROI (chalk warning: NBA favorites win ~68%%+ -> win%% is NOT edge) ===")
for rk,net,name,wallet,n,nsum,nreg,w,l,v,winp,roi,avgb,lastd,trunc in sorted(rows,reverse=True):
    fl=[]
    if nreg<15: fl.append("TINY-REG-N")
    if nsum>nreg: fl.append("SUMMER-HEAVY")
    if trunc: fl.append("TRUNCATED")
    if (winp==winp and winp>=64 and (roi!=roi or roi<3)): fl.append("CHALK")
    print("  %-13s ROI=%6.1f%% NET=$%+11.2f n=%d(sum %d/reg %d) W-L=%d-%d win%%=%2.0f last=%s %s"%(
        name,(roi if roi==roi else -999),net,n,nsum,nreg,w,l,(winp if winp==winp else -1),lastd," ".join(fl)))
print("\nNET=SELL+REDEEM-BUY over resolved nba single-games (losers -BUY). Ranked by NET ROI. reg=regular/playoff,")
print("sum=summer/preseason(Jul-Oct). n(reg)<15 NOT a verdict. Data=2024-25 season (~13mo old). TRUNCATED=partial.")
PY
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$enc = New-Object Text.UTF8Encoding($false)
$tf  = Join-Path $env:TEMP 'pk_nbas_chunk.sh'
$size = 20000; $first = $true
for ($i = 0; $i -lt $b64.Length; $i += $size) {
    $chunk = $b64.Substring($i, [Math]::Min($size, $b64.Length - $i))
    $op = if ($first) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pk_nbas.b64`n", $enc)
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $first = $false
}
[IO.File]::WriteAllText($tf, "base64 -d /tmp/pk_nbas.b64 | bash`n", $enc)
Write-Host "== NBA WHALE NET SCORING STAGE 2 (READ-ONLY) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Remove-Item $tf -ErrorAction SilentlyContinue
