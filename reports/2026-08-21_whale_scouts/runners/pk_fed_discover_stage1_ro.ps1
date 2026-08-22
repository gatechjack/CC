# pk_fed_discover_stage1_ro.ps1 -- READ-ONLY Fed-rates whale DISCOVERY (Stage 1). NO writes.
# Enumerate Poly per-meeting Fed events (tag_id=100196, fed-decision/fed-interest-rates, exclude ecb/
# cumulative/dissent), aggregate trader wallets from /trades. Rank by # distinct FOMC meetings.
# Run: powershell -ep bypass -f .\pk_fed_discover_stage1_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
venv/bin/python3 - <<'PY'
import json, urllib.request
def http(url, timeout=30):
    req=urllib.request.Request(url, headers={"User-Agent":"research/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read().decode())
GAMMA="https://gamma-api.polymarket.com"; DATA="https://data-api.polymarket.com"
def is_fed_meeting(eslug):
    e=eslug or ""
    if "ecb" in e: return False
    if e.startswith("fed-rate-cut-by"): return False
    return e.startswith("fed-decision-in") or e.startswith("fed-interest-rates-")
# meeting-key from event slug (month-year-ish) for distinct-meeting count
def mkey(eslug):
    return eslug.replace("fed-decision-in-","").replace("fed-interest-rates-","")
markets=[]   # (meeting, mslug, cid)
meetings=set(); dropped_dissent=0
for off in range(0,700,100):
    try: evs=http("%s/events?tag_id=100196&closed=true&limit=100&offset=%d&order=endDate&ascending=false"%(GAMMA,off))
    except Exception as e: print("events off=%d ERR %s"%(off,str(e)[:50])); break
    if not evs: break
    for e in evs:
        es=e.get("slug","") or ""
        if not is_fed_meeting(es): continue
        mk=mkey(es); meetings.add(mk)
        for m in (e.get("markets") or []):
            ms=m.get("slug","") or ""; cid=m.get("conditionId") or m.get("condition_id")
            if not cid: continue
            if "dissent" in ms: dropped_dissent+=1; continue
            markets.append((mk, ms, cid))
print("distinct FOMC meetings=%d  per-meeting band markets=%d  (dropped dissent-granular=%d)" % (len(meetings), len(markets), dropped_dissent))
print("meetings:", sorted(meetings))
wal={}
for i,(mk,ms,cid) in enumerate(markets):
    try: trades=http("%s/trades?market=%s&limit=500"%(DATA,cid))
    except Exception: continue
    if not isinstance(trades,list): continue
    for t in trades:
        w=t.get("proxyWallet") or t.get("user") or t.get("maker")
        if not w: continue
        try: notion=float(t.get("size",0) or 0)*float(t.get("price",0) or 0)
        except Exception: notion=0.0
        dd=wal.setdefault(w, {"meets":set(),"notional":0.0,"trades":0,"name":t.get("name") or t.get("pseudonym") or ""})
        dd["meets"].add(mk); dd["notional"]+=notion; dd["trades"]+=1
    if (i+1)%20==0: print("  ...%d/%d markets, %d wallets"%(i+1,len(markets),len(wal)))
cand=[(w,len(d["meets"]),round(d["notional"],2),d["trades"],d["name"]) for w,d in wal.items()]
cand.sort(key=lambda x:(x[1],x[2]), reverse=True)
print("\n=== TOP FED-RATES CANDIDATES (wallet | #meetings | notional | trades | name) ===")
print("markets sampled=%d distinct wallets=%d" % (len(markets), len(wal)))
for w,nm,notion,ntr,nme in cand[:30]:
    print("  %s meets=%-2d notional=$%-11.2f trades=%-4d %s" % (w,nm,notion,ntr,(nme or "")[:18]))
print("\nrepeat rates bettors (>=5 meetings): %d" % len([c for c in cand if c[1]>=5]))
print("CAVEAT: LOW-FREQ category (n small by nature, NOT a data failure). first 500 trades/market. notional=gross NOT P&L. Stage 2 = NET.")
PY
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$enc = New-Object Text.UTF8Encoding($false)
$tf  = Join-Path $env:TEMP 'pk_feddisc_chunk.sh'
$size = 20000; $first = $true
for ($i = 0; $i -lt $b64.Length; $i += $size) {
    $chunk = $b64.Substring($i, [Math]::Min($size, $b64.Length - $i))
    $op = if ($first) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pk_feddisc.b64`n", $enc)
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $first = $false
}
[IO.File]::WriteAllText($tf, "base64 -d /tmp/pk_feddisc.b64 | bash`n", $enc)
Write-Host "== FED-RATES WHALE DISCOVERY STAGE 1 (READ-ONLY) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Remove-Item $tf -ErrorAction SilentlyContinue
