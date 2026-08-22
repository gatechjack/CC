# pk_nba_discover_stage1_ro.ps1 -- READ-ONLY NBA whale DISCOVERY (Stage 1). NO writes.
# Enumerate NBA single-game moneyline via Polymarket series_id=2 (closed), aggregate trader wallets from
# /trades?market=<cid>. Rank by participation for Stage-2 NET scoring. Only nba-{away}-{home}-YYYY-MM-DD.
# Run: powershell -ep bypass -f .\pk_nba_discover_stage1_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
venv/bin/python3 - <<'PY'
import json, urllib.request, re
from collections import Counter
def http(url, timeout=30):
    req=urllib.request.Request(url, headers={"User-Agent":"research/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read().decode())
GAMMA="https://gamma-api.polymarket.com"; DATA="https://data-api.polymarket.com"
GAME=re.compile(r"^nba-[a-z0-9]+-[a-z0-9]+-\d{4}-\d{2}-\d{2}$")
def era(d):
    mm=d[5:7]
    if mm in ("07","08","09","10"): return "preseason/summer"
    if mm in ("04","05","06"): return "playoffs"
    return "regular"
resolved=[]; seen=set()
for off in range(0,1500,100):
    try: evs=http("%s/events?series_id=2&closed=true&limit=100&offset=%d&order=endDate&ascending=false" % (GAMMA, off))
    except Exception as e: print("events off=%d ERR %s"%(off,str(e)[:50])); break
    if not evs: break
    for e in evs:
        for m in (e.get("markets") or []):
            ms=m.get("slug","") or ""; cid=m.get("conditionId") or m.get("condition_id")
            if not cid or cid in seen or not GAME.match(ms): continue
            seen.add(cid); resolved.append((ms[-10:], ms, cid))
    if len(resolved)>=220: break
resolved.sort(reverse=True)
print("nba single-game markets=%d" % len(resolved))
if resolved: print("date range:", resolved[-1][0], "->", resolved[0][0])
print("by era:", dict(Counter(era(d) for d,_,_ in resolved)))
wal={}
for i,(d,ms,cid) in enumerate(resolved):
    try: trades=http("%s/trades?market=%s&limit=500"%(DATA,cid))
    except Exception: continue
    if not isinstance(trades,list): continue
    for t in trades:
        w=t.get("proxyWallet") or t.get("user") or t.get("maker")
        if not w: continue
        try: notion=float(t.get("size",0) or 0)*float(t.get("price",0) or 0)
        except Exception: notion=0.0
        dd=wal.setdefault(w, {"mkts":set(),"notional":0.0,"trades":0,"name":t.get("name") or t.get("pseudonym") or ""})
        dd["mkts"].add(cid); dd["notional"]+=notion; dd["trades"]+=1
    if (i+1)%30==0: print("  ...%d/%d markets, %d wallets"%(i+1,len(resolved),len(wal)))
cand=[(w,len(d["mkts"]),round(d["notional"],2),d["trades"],d["name"]) for w,d in wal.items()]
cand.sort(key=lambda x:(x[1],x[2]), reverse=True)
print("\n=== TOP NBA-PARTICIPATION CANDIDATES (wallet | mkts | notional | trades | name) ===")
print("markets sampled=%d distinct wallets=%d" % (len(resolved), len(wal)))
for w,nm,notion,ntr,nme in cand[:35]:
    print("  %s mkts=%-3d notional=$%-11.2f trades=%-4d %s" % (w,nm,notion,ntr,(nme or "")[:18]))
print("\nrepeat NBA bettors (>=8 games): %d" % len([c for c in cand if c[1]>=8]))
print("CAVEAT: 2024-25-season data (~13mo old); first 500 trades/market; notional=gross NOT P&L; Stage 2 = NET.")
PY
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$enc = New-Object Text.UTF8Encoding($false)
$tf  = Join-Path $env:TEMP 'pk_nbadisc_chunk.sh'
$size = 20000; $first = $true
for ($i = 0; $i -lt $b64.Length; $i += $size) {
    $chunk = $b64.Substring($i, [Math]::Min($size, $b64.Length - $i))
    $op = if ($first) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pk_nbadisc.b64`n", $enc)
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $first = $false
}
[IO.File]::WriteAllText($tf, "base64 -d /tmp/pk_nbadisc.b64 | bash`n", $enc)
Write-Host "== NBA WHALE DISCOVERY STAGE 1 (READ-ONLY) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Remove-Item $tf -ErrorAction SilentlyContinue
