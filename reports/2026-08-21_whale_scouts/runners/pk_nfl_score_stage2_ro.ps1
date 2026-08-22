# pk_nfl_score_stage2_ro.ps1 -- READ-ONLY NFL whale NET scoring (Stage 2). NO writes.
# NET = SELL+REDEEM-BUY over RESOLVED nfl single-game decisions (losers = -BUY). Win/loss from real
# outcomePrices winner vs outcome bought. Ranks by NET ROI (NOT win% -- NFL chalk warning). Flags
# preseason-heavy / tiny-n / truncated / futures. Run: powershell -ep bypass -f .\pk_nfl_score_stage2_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
venv/bin/python3 - <<'PY'
import json, urllib.request, re, datetime
def http(url, timeout=30):
    req=urllib.request.Request(url, headers={"User-Agent":"research/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read().decode())
DATA="https://data-api.polymarket.com"; GAMMA="https://gamma-api.polymarket.com"
GAME=re.compile(r"^nfl-[a-z0-9]+-[a-z0-9]+-\d{4}-\d{2}-\d{2}$")
def dt_of(slug): return slug[-10:] if GAME.match(slug) else None
def is_pre(d): return d[5:7] in ("07","08")
CAND=[
 ("0x4a64afa45a44a01890c2161be88d2b44751d4430","0x4a64_612007"),
 ("0xf0b0ef1d6320c6be896b4c9c54dd74407e7f8cab","daroghi"),
 ("0xd3989ba133ab48b5b3a81e3dba9b37b5966a46d7","semi"),
 ("0x026985b67ade7ba199698b9d7d0451a22214cb2d","Milio"),
 ("0xa9d71818dadc207f9ea3d3f46ff0b12e497025e9","t2o2"),
 ("0x4eb85167e92a59b671478bbdaaa901551a6a9231","Dr.Awkcab"),
 ("0x371a0d623144ad877c81614afe52c356619c34b0","St-Qc"),
 ("0x2fe6d3037aab8ca66fc3a43918d9028a601aab9d","Jolanda"),
 ("0x3d6989543c6d69357277bc9f3b37d4e070cb9829","0x3D69"),
 ("0x2ff0e36eb9632a47c3eb75224bba1f141a6346e3","0x2ff0"),
 ("0x45e140d754534bfcde516c1faf3acacdef55f42d","Jl2024"),
 ("0x164229b9d0587e50b94098f5840164282b565988","1mperator17"),
 ("0xa6a856a8c8a7f14fd9be6ae11c367c7cbb755009","BetMechanic"),
 ("0x75e091ca3f8e5481c2166c82fba0669e3a65fe50","FordBronco"),
 ("0x2b41a72490ca2af2709e34368e6697cdd32fe0d3","MasterNewbie"),
 ("0xe20ef5c967504bcf1651ac1f4b4e4ce31e825998","Bang"),
 ("0xe9b8005c55e8ef2c311fbbd427521c981828cc82","SadMan"),
 ("0xae763a6bdd92761cf24792c3e1116fe5df52dce5","test99"),
 ("0x2fb0f88ef5ba40e799b996e6f07b590d92b4abf8","AIisTheNewWD"),
 ("0x9f85845d67a69afa2b37f6fefd8d0d95912fd745","Gamesetmatch"),
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
print("wallet          n  pre/reg  W-L-V  win%   NET_usdc      ROI%    avg_buy last_nfl  trunc")
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
        d=g.setdefault(cid,{"slug":slug,"buy":0.0,"sell":0.0,"redeem":0.0,"ts":0,"obuy":{},"pre":is_pre(slug[-10:])})
        typ=(r.get("type","") or "").upper(); side=(r.get("side","") or "").upper(); a_=amt(r)
        if typ=="TRADE" and side=="BUY":
            d["buy"]+=a_; oc=r.get("outcome","") or ""; d["obuy"][oc]=d["obuy"].get(oc,0.0)+a_
        elif typ=="TRADE" and side=="SELL": d["sell"]+=a_
        elif typ=="REDEEM": d["redeem"]+=a_
        d["ts"]=max(d["ts"], int(r.get("timestamp",0) or 0))
    win=resolve(g.keys())
    n=npre=nreg=w=l=v=0; net=0.0; buys=0.0; last=0
    for cid,d in g.items():
        wl=win.get(cid)
        if wl is None: continue
        n+=1; buys+=d["buy"]; net+=d["sell"]+d["redeem"]-d["buy"]; last=max(last,d["ts"])
        if d["pre"]: npre+=1
        else: nreg+=1
        bet=max(d["obuy"].items(), key=lambda kv: kv[1])[0] if d["obuy"] else ""
        if wl=="VOID": v+=1
        elif bet and bet==wl: w+=1
        else: l+=1
    if n==0:
        print("  %-14s 0   -        -       -     -             -       -       -         %s"%(name,"Y" if trunc else "-")); continue
    winp=(100.0*w/(w+l)) if (w+l) else float("nan")
    roi=(100.0*net/buys) if buys>0 else float("nan")
    avgb=buys/n
    lastd=datetime.datetime.fromtimestamp(last, datetime.timezone.utc).strftime("%Y-%m-%d") if last else "?"
    rows.append((roi if roi==roi else -1e9, net, name, wallet, n, npre, nreg, w, l, v, winp, roi, avgb, lastd, trunc))
    print("  %-14s %-3d %d/%-6d %d-%d-%-2d %5.0f %+12.2f %7.1f %7.0f %s  %s"%(
        name,n,npre,nreg,w,l,v,(winp if winp==winp else -1),net,(roi if roi==roi else -999),avgb,lastd,"Y" if trunc else "-"))
print("\n=== RANKED BY NET ROI (chalk warning: NFL favorites win ~66%% -> win%% alone is NOT edge) ===")
for rk,net,name,wallet,n,npre,nreg,w,l,v,winp,roi,avgb,lastd,trunc in sorted(rows,reverse=True):
    fl=[]
    if nreg<15: fl.append("TINY-REG-N")
    if npre>nreg: fl.append("PRESEASON-HEAVY")
    if trunc: fl.append("TRUNCATED")
    if (winp==winp and winp>=62 and (roi!=roi or roi<3)): fl.append("CHALK(hi-win/lo-ROI)")
    print("  %-14s ROI=%6.1f%% NET=$%+10.2f n=%d(pre %d/reg %d) W-L=%d-%d win%%=%2.0f last=%s %s"%(
        name,(roi if roi==roi else -999),net,n,npre,nreg,w,l,(winp if winp==winp else -1),lastd," ".join(fl)))
print("\nNET=SELL+REDEEM-BUY over resolved nfl single-games (losers -BUY). Ranked by NET ROI. reg=regular/playoff,")
print("pre=preseason(Jul/Aug, noisy). n(reg)<15 NOT a verdict. Data=2024-25 season (~1yr old). TRUNCATED=partial.")
PY
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$enc = New-Object Text.UTF8Encoding($false)
$tf  = Join-Path $env:TEMP 'pk_nfls_chunk.sh'
$size = 20000; $first = $true
for ($i = 0; $i -lt $b64.Length; $i += $size) {
    $chunk = $b64.Substring($i, [Math]::Min($size, $b64.Length - $i))
    $op = if ($first) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pk_nfls.b64`n", $enc)
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $first = $false
}
[IO.File]::WriteAllText($tf, "base64 -d /tmp/pk_nfls.b64 | bash`n", $enc)
Write-Host "== NFL WHALE NET SCORING STAGE 2 (READ-ONLY) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Remove-Item $tf -ErrorAction SilentlyContinue
