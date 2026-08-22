# pk_ufc_discover_stage1_ro.ps1 -- READ-ONLY UFC whale DISCOVERY (Stage 1, v2). NO writes/orders.
# Query UFC events by tag_id=279 (dedicated UFC tag), filter to FIGHT MONEYLINES (slug ends -YYYY-MM-DD,
# excludes -go-the-distance props + who-will futures), aggregate trader wallets from data-api
# /trades?market=<cid>. Rank candidates by UFC participation (breadth x notional) for Stage-2 NET scoring.
# Run: powershell -ep bypass -f .\pk_ufc_discover_stage1_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
venv/bin/python3 - <<'PY'
import json, urllib.request, re
def http(url, timeout=25):
    req=urllib.request.Request(url, headers={"User-Agent":"research/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())
GAMMA="https://gamma-api.polymarket.com"; DATA="https://data-api.polymarket.com"
DATE=re.compile(r"-\d{4}-\d{2}-\d{2}$")
def is_fight_ml(slug):
    s=slug or ""
    return s.startswith("ufc-") and DATE.search(s) is not None and "-go-the-distance" not in s

# ---- collect resolved UFC fight moneyline markets via tag_id=279 ----
resolved=[]; seen=set(); no_mkts=0
for off in range(0, 1200, 100):
    try:
        evs=http("%s/events?tag_id=279&closed=true&limit=100&offset=%d&order=endDate&ascending=false" % (GAMMA, off))
    except Exception as e:
        print("events off=%d ERR %s" % (off, str(e)[:60])); break
    if not evs: break
    for e in evs:
        mkts=e.get("markets") or []
        if not mkts: no_mkts+=1
        for m in mkts:
            ms=m.get("slug","") or ""; cid=m.get("conditionId") or m.get("condition_id")
            if not cid or cid in seen or not is_fight_ml(ms): continue
            seen.add(cid); resolved.append((ms, cid, m.get("outcomePrices")))
    if len(resolved) >= 90: break
print("resolved_ufc_fight_markets=%d  (events_with_no_markets=%d)" % (len(resolved), no_mkts))
for ms,cid,op in resolved[:6]:
    print("   ", ms, cid[:14], "res=", op)

# ---- aggregate trader wallets ----
wal={}
for i,(ms,cid,op) in enumerate(resolved):
    try:
        trades=http("%s/trades?market=%s&limit=500" % (DATA, cid))
    except Exception as e:
        continue
    if not isinstance(trades, list): continue
    for t in trades:
        w=t.get("proxyWallet") or t.get("user") or t.get("maker")
        if not w: continue
        try: notion=float(t.get("size",0) or 0)*float(t.get("price",0) or 0)
        except Exception: notion=0.0
        d=wal.setdefault(w, {"mkts":set(),"notional":0.0,"trades":0,"name":t.get("name") or t.get("pseudonym") or ""})
        d["mkts"].add(cid); d["notional"]+=notion; d["trades"]+=1
    if (i+1)%20==0: print("  ...%d/%d markets, %d wallets" % (i+1, len(resolved), len(wal)))

cand=[(w, len(d["mkts"]), round(d["notional"],2), d["trades"], d["name"]) for w,d in wal.items()]
cand.sort(key=lambda x:(x[1], x[2]), reverse=True)
print("\n=== TOP UFC-PARTICIPATION CANDIDATES (wallet | mkts | notional_usdc | trades | name) ===")
print("markets sampled=%d  distinct wallets=%d" % (len(resolved), len(wal)))
for w,nm,notion,ntr,nme in cand[:35]:
    print("  %s mkts=%-3d notional=$%-10.2f trades=%-4d %s" % (w, nm, notion, ntr, (nme or "")[:18]))
mkts4=[c for c in cand if c[1]>=4]
print("\nrepeat UFC bettors (>=4 distinct fights): %d" % len(mkts4))
print("CAVEAT: first 500 trades/market on %d recent resolved fights; notional=gross flow NOT P&L; Stage 2 = NET." % len(resolved))
PY
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$enc = New-Object Text.UTF8Encoding($false)
$tf  = Join-Path $env:TEMP 'pk_ufcd_chunk.sh'
$size = 20000; $first = $true
for ($i = 0; $i -lt $b64.Length; $i += $size) {
    $chunk = $b64.Substring($i, [Math]::Min($size, $b64.Length - $i))
    $op = if ($first) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pk_ufcd.b64`n", $enc)
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $first = $false
}
[IO.File]::WriteAllText($tf, "base64 -d /tmp/pk_ufcd.b64 | bash`n", $enc)
Write-Host "== UFC WHALE DISCOVERY STAGE 1 v2 (READ-ONLY) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Remove-Item $tf -ErrorAction SilentlyContinue
