# pk_nfl_diag2_ro.ps1 -- READ-ONLY: get NFL series_id (enumeration handle) + test /trades trader-discovery
# on playoff / 2025-regular / 2026-preseason NFL markets. NO writes.
# Run: powershell -ep bypass -f .\pk_nfl_diag2_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
venv/bin/python3 - <<'PY'
import json, urllib.request
def http(u,t=30):
    r=urllib.request.Request(u, headers={"User-Agent":"research/1.0"})
    with urllib.request.urlopen(r,timeout=t) as x: return json.loads(x.read().decode())
GAMMA="https://gamma-api.polymarket.com"; DATA="https://data-api.polymarket.com"
def ev_by_slug(slug):
    d=http("%s/events?slug=%s" % (GAMMA, slug))
    if isinstance(d,list) and d: return d[0]
    if isinstance(d,dict) and d.get("slug"): return d
    return None
SLUGS=["nfl-dailies-2024-01-21","nfl-dailies-2025-11-16","nfl-dailies-2025-11-17",
       "nfl-dailies-2025-12-14","nfl-dailies-2026-08-20","nfl-dailies-2026-08-21"]
series_ids=set()
for slug in SLUGS:
    try: e=ev_by_slug(slug)
    except Exception as ex: print(slug,"ERR",str(ex)[:50]); continue
    if not e: print(slug,"-> not found"); continue
    tags=[(t.get("id"),t.get("label")) for t in (e.get("tags") or [])]
    ser=e.get("series")
    sers=[(s.get("id"),s.get("slug")) for s in ser] if isinstance(ser,list) else ser
    if isinstance(ser,list):
        for s in ser:
            if s.get("id"): series_ids.add(str(s.get("id")))
    mkts=[(m.get("slug"), m.get("conditionId") or m.get("condition_id")) for m in (e.get("markets") or [])]
    print("%s -> markets=%d tags=%s series=%s" % (slug, len(mkts), tags, sers))
    # /trades on the first real game market
    for ms,cid in mkts:
        if cid and ms and ms.startswith("nfl-") and ms[-10:].count("-")==2:
            try:
                tr=http("%s/trades?market=%s&limit=500"%(DATA,cid))
                n=len(tr) if isinstance(tr,list) else 0
                w=len(set(t.get("proxyWallet") for t in tr if isinstance(t,dict) and t.get("proxyWallet"))) if isinstance(tr,list) else 0
                print("      /trades %s -> trades=%d wallets=%d" % (ms, n, w))
            except Exception as ex: print("      /trades %s ERR %s"%(ms,str(ex)[:50]))
            break
print("\nNFL series_id(s) seen:", series_ids)
# test enumerating by the series id
for sid in series_ids:
    try:
        evs=http("%s/events?series_id=%s&closed=true&limit=10&order=endDate&ascending=false" % (GAMMA, sid))
        n=len(evs) if isinstance(evs,list) else 0
        slugs=[e.get("slug") for e in (evs or [])][:5]
        print("  series_id=%s closed events=%d sample=%s" % (sid, n, slugs))
    except Exception as ex: print("  series_id=%s ERR %s"%(sid,str(ex)[:60]))
PY
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$enc = New-Object Text.UTF8Encoding($false)
$tf  = Join-Path $env:TEMP 'pk_nfld2_chunk.sh'
$size = 20000; $first = $true
for ($i = 0; $i -lt $b64.Length; $i += $size) {
    $chunk = $b64.Substring($i, [Math]::Min($size, $b64.Length - $i))
    $op = if ($first) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pk_nfld2.b64`n", $enc)
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $first = $false
}
[IO.File]::WriteAllText($tf, "base64 -d /tmp/pk_nfld2.b64 | bash`n", $enc)
Write-Host "== NFL SERIES-ID + /trades DIAGNOSTIC (READ-ONLY) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Remove-Item $tf -ErrorAction SilentlyContinue
