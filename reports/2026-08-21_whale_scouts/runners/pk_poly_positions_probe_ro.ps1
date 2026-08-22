# pk_poly_positions_probe_ro.ps1 -- READ-ONLY characterization of Polymarket /closed-positions + /positions
# as a copy-DB foundation. Full raw field dump + completeness/cap test on high-volume wallets + category
# spread + freshness. NO writes. Run: powershell -ep bypass -f .\pk_poly_positions_probe_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
venv/bin/python3 - <<'PY'
import json, urllib.request, datetime
def http(url, timeout=30):
    req=urllib.request.Request(url, headers={"User-Agent":"research/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.getcode(), json.loads(r.read().decode())
DATA="https://data-api.polymarket.com"
def closed(w,limit=50,offset=0):
    return http("%s/closed-positions?user=%s&limit=%d&offset=%d"%(DATA,w,limit,offset))
W={"scanner":"0x989b67c86daa5675c2a7d0ee4107d2a38f628ef3",
   "cigarettes":"0xd218e474776403a330142299f7796e8ba32eb5c9",
   "S-Works":"0xee00ba338c59557141789b127927a55f5cc5cea1",
   "Kh4mz4t":"0x52f454c43b23504d2dc39e034bf19469fd592b15"}

print("===== 1. FULL RAW FIELD DUMP (/closed-positions, scanner) =====")
code,rows=closed(W["scanner"],50,0)
print("http=%d rows=%d"%(code,len(rows)))
if rows:
    print("SAMPLE closed-position row (all fields):")
    print(json.dumps(rows[0], indent=1, default=str))
    keys=set()
    for r in rows: keys.update(r.keys())
    print("\nALL KEYS union across page:", sorted(keys))

print("\n===== 2. FULL RAW FIELD DUMP (/positions OPEN, scanner) =====")
try:
    code,op=http("%s/positions?user=%s"%(DATA,W["scanner"]))
    print("http=%d open_positions=%d"%(code,len(op)))
    if op: print("SAMPLE open position (all fields):"); print(json.dumps(op[0], indent=1, default=str)); print("OPEN KEYS:", sorted(op[0].keys()))
except Exception as e: print("positions ERR", str(e)[:80])

print("\n===== 3. COMPLETENESS / CAP TEST (paginate to end; high-vol wallets that truncated under activity) =====")
for name in ("cigarettes","S-Works","scanner","Kh4mz4t"):
    w=W[name]; total=0; cats={}; tsmin=None; tsmax=None; last_off=0; full_pages=0; hit_empty=False
    for off in range(0,3050,50):
        try: code,r=closed(w,50,off)
        except Exception as e: print("  %s off=%d ERR %s"%(name,off,str(e)[:40])); break
        if not r: hit_empty=True; break
        total+=len(r); last_off=off
        if len(r)==50: full_pages+=1
        for x in r:
            es=(x.get("eventSlug") or x.get("slug") or "") or "?"
            cat=es.split("-")[0]
            cats[cat]=cats.get(cat,0)+1
            ts=x.get("timestamp")
            if ts:
                tsmin=ts if tsmin is None else min(tsmin,ts); tsmax=ts if tsmax is None else max(tsmax,ts)
        if len(r)<50: break
    def d(ts): return datetime.datetime.fromtimestamp(ts,datetime.timezone.utc).strftime("%Y-%m-%d") if ts else "?"
    capnote = "CAP-HIT (full pages then empty at offset %d)"%last_off if (hit_empty and full_pages*50>=total-1 and total>=1000) else ("complete (last page partial)" )
    print("  %-11s total_closed_positions=%-5d last_offset=%-4d resolution_dates=%s..%s  %s"%(name,total,last_off,d(tsmin),d(tsmax),capnote))
    top=sorted(cats.items(), key=lambda kv:kv[1], reverse=True)[:10]
    print("       category spread (eventSlug prefix -> count):", top)
PY
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$enc = New-Object Text.UTF8Encoding($false)
$tf  = Join-Path $env:TEMP 'pk_pospro_chunk.sh'
$size = 20000; $first = $true
for ($i = 0; $i -lt $b64.Length; $i += $size) {
    $chunk = $b64.Substring($i, [Math]::Min($size, $b64.Length - $i))
    $op = if ($first) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pk_pospro.b64`n", $enc)
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $first = $false
}
[IO.File]::WriteAllText($tf, "base64 -d /tmp/pk_pospro.b64 | bash`n", $enc)
Write-Host "== POLYMARKET POSITIONS API CHARACTERIZATION (READ-ONLY) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Remove-Item $tf -ErrorAction SilentlyContinue
