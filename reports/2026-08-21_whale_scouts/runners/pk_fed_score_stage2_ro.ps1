# pk_fed_score_stage2_ro.ps1 -- READ-ONLY Fed-rates whale NET scoring (Stage 2). NO writes.
# NET = SELL+REDEEM-BUY over RESOLVED per-meeting Fed band bets. Win/loss from outcomePrices. Ranks by
# NET ROI. Fed-specific: avg WIN entry price (win_px) = chalk-vs-surprise signal (high~priced-in/chalk,
# low~called contested = edge). Run: powershell -ep bypass -f .\pk_fed_score_stage2_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
venv/bin/python3 - <<'PY'
import json, urllib.request, re, datetime
def http(url, timeout=30):
    req=urllib.request.Request(url, headers={"User-Agent":"research/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read().decode())
DATA="https://data-api.polymarket.com"; GAMMA="https://gamma-api.polymarket.com"
MONTHS=("january","february","march","april","may","june","july","august","september","october","november","december")
def is_fed_meeting_mkt(slug):
    s=slug or ""
    if "ecb" in s or "dissent" in s or "rate-cut-by" in s: return False
    if "fed" not in s or "meeting" not in s: return False
    return ("bps" in s) or ("no-change" in s) or ("no change" in s)
def meeting_key(slug):
    mo=next((m for m in MONTHS if m in slug), "?")
    yr=re.search(r"20\d\d", slug); return mo + (yr.group(0) if yr else "")
CAND=[
 ("0x24c8cf69a0e0a17eee21f69d29752bfa32e823e1","debased"),
 ("0xd218e474776403a330142299f7796e8ba32eb5c9","cigarettes"),
 ("0x8a7b576b7a53ef54876f9eca18bcd84f12d8782f","0xB16B00B5"),
 ("0x8e9eedf20dfa70956d49f608a205e402d9df38e4","Siziriv"),
 ("0x48185887c8dc95de60ee89722f1d0ee7894cbf0b","Liquidifier"),
 ("0xbc54e69667ceb6ccec538e5a0ba1927fc1fe680f","Donkov"),
 ("0x63d43bbb87f85af03b8f2f9e2fad7b54334fa2f1","wokerjoesleeper"),
 ("0x5f390e4b7d6f06d6756a6c92afdbf7b3176aa78c","oVyg7f"),
 ("0xd1acd3925d895de9aec98ff95f3a30c5279d08d5","Kickstand7"),
 ("0x6ffb4354cbe6e0f9989e3b55564ec5fb8646a834","AgriSecretary"),
 ("0x71edffd0d70a1da823ff07a3c6fc81457294d338","pako"),
 ("0xde242261bcd8d4320113f12230da34d705ca25a8","PolymaREKT"),
 ("0x3c918a8ef8c379304f744592120cbb4d76149af7","Vilgefortz"),
 ("0x43372356634781eea88d61bbdd7824cdce958882","Anjun"),
 ("0x989b67c86daa5675c2a7d0ee4107d2a38f628ef3","scanner"),
 ("0x000d257d2dc7616feaef4ae0f14600fdf50a758e","scottilicious"),
 ("0xc8ab97a9089a9ff7e6ef0688e6e591a066946418","ArmageddonRB"),
 ("0x7f692340bcc1d90b3ca3c8436e3973adb0279c7a","meowinglion"),
 ("0x71ed0bc95433cdf1be29f43219725fce9addd9eb","d1k21"),
 ("0xf0b0ef1d6320c6be896b4c9c54dd74407e7f8cab","daroghi"),
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
print("wallet          n   mtgs W-L-V  win%   NET_usdc     ROI%   avg_buy win_px last_fed  trunc")
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
        if not is_fed_meeting_mkt(slug): continue
        cid=r.get("conditionId") or r.get("condition_id")
        if not cid: continue
        d=g.setdefault(cid,{"slug":slug,"buy":0.0,"sell":0.0,"redeem":0.0,"ts":0,"obuy":{},"px":{}})
        typ=(r.get("type","") or "").upper(); side=(r.get("side","") or "").upper(); a_=amt(r)
        if typ=="TRADE" and side=="BUY":
            d["buy"]+=a_; oc=r.get("outcome","") or ""; d["obuy"][oc]=d["obuy"].get(oc,0.0)+a_
            try: d["px"][oc]=float(r.get("price",0) or 0)
            except Exception: pass
        elif typ=="TRADE" and side=="SELL": d["sell"]+=a_
        elif typ=="REDEEM": d["redeem"]+=a_
        d["ts"]=max(d["ts"], int(r.get("timestamp",0) or 0))
    win=resolve(g.keys())
    n=w=l=v=0; net=0.0; buys=0.0; last=0; meets=set(); winpx=[]
    for cid,d in g.items():
        wl=win.get(cid)
        if wl is None: continue
        n+=1; buys+=d["buy"]; net+=d["sell"]+d["redeem"]-d["buy"]; last=max(last,d["ts"]); meets.add(meeting_key(d["slug"]))
        bet=max(d["obuy"].items(), key=lambda kv: kv[1])[0] if d["obuy"] else ""
        if wl=="VOID": v+=1
        elif bet and bet==wl:
            w+=1
            if bet in d["px"]: winpx.append(d["px"][bet])
        else: l+=1
    if n==0:
        print("  %-14s 0   -    -       -     -            -      -       -      -         %s"%(name,"Y" if trunc else "-")); continue
    winp=(100.0*w/(w+l)) if (w+l) else float("nan")
    roi=(100.0*net/buys) if buys>0 else float("nan")
    avgb=buys/n; wpx=(sum(winpx)/len(winpx)) if winpx else float("nan")
    lastd=datetime.datetime.fromtimestamp(last, datetime.timezone.utc).strftime("%Y-%m-%d") if last else "?"
    rows.append((roi if roi==roi else -1e9, net, name, wallet, n, len(meets), w, l, v, winp, roi, avgb, wpx, lastd, trunc))
    print("  %-14s %-3d %-4d %d-%d-%-2d %5.0f %+12.2f %6.1f %7.0f %5.2f %s  %s"%(
        name,n,len(meets),w,l,v,(winp if winp==winp else -1),net,(roi if roi==roi else -999),avgb,(wpx if wpx==wpx else 0),lastd,"Y" if trunc else "-"))
print("\n=== RANKED BY NET ROI (chalk = market-implied prob; win_px = avg entry price on WINS) ===")
for rk,net,name,wallet,n,nm,w,l,v,winp,roi,avgb,wpx,lastd,trunc in sorted(rows,reverse=True):
    fl=[]
    if n<8: fl.append("THIN-N")
    if wpx==wpx and wpx>=0.85 and (roi!=roi or roi<3): fl.append("CHALK(won-priced-in)")
    if wpx==wpx and wpx<0.70 and (roi==roi and roi>0): fl.append("EDGE?(won-contested)")
    if trunc: fl.append("TRUNCATED")
    print("  %-14s ROI=%6.1f%% NET=$%+11.2f n=%d mtgs=%d W-L=%d-%d win%%=%2.0f win_px=%.2f last=%s %s"%(
        name,(roi if roi==roi else -999),net,n,nm,w,l,(winp if winp==winp else -1),(wpx if wpx==wpx else 0),lastd," ".join(fl)))
print("\nNET=SELL+REDEEM-BUY over resolved per-meeting Fed band bets (losers -BUY). Ranked by NET ROI.")
print("win_px = avg entry price on WON bets: >=0.85 = won obvious/priced-in (chalk); <0.70 = called contested (edge).")
print("Low-freq category: n small by nature. Data 2024-05..2026-07 (CURRENT). n<8 = thin (structural, not a data failure).")
PY
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$enc = New-Object Text.UTF8Encoding($false)
$tf  = Join-Path $env:TEMP 'pk_feds_chunk.sh'
$size = 20000; $first = $true
for ($i = 0; $i -lt $b64.Length; $i += $size) {
    $chunk = $b64.Substring($i, [Math]::Min($size, $b64.Length - $i))
    $op = if ($first) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pk_feds.b64`n", $enc)
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $first = $false
}
[IO.File]::WriteAllText($tf, "base64 -d /tmp/pk_feds.b64 | bash`n", $enc)
Write-Host "== FED-RATES WHALE NET SCORING STAGE 2 (READ-ONLY) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Remove-Item $tf -ErrorAction SilentlyContinue
