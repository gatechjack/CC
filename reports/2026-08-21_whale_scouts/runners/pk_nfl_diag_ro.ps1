# pk_nfl_diag_ro.ps1 -- READ-ONLY: find the NFL tag/series for enumeration + test whether /trades
# returns traders on RECENT vs REGULAR-SEASON vs OLD NFL markets (the discovery-window make-or-break).
# NO writes. Run: powershell -ep bypass -f .\pk_nfl_diag_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
venv/bin/python3 - <<'PY'
import json, urllib.request, re
from collections import Counter
def http(u,t=30):
    r=urllib.request.Request(u, headers={"User-Agent":"research/1.0"})
    with urllib.request.urlopen(r,timeout=t) as x: return json.loads(x.read().decode())
GAMMA="https://gamma-api.polymarket.com"; DATA="https://data-api.polymarket.com"
GAME=re.compile(r"^nfl-[a-z]+-[a-z]+-\d{4}-\d{2}-\d{2}$")   # nfl-{away}-{home}-YYYY-MM-DD
# fetch recent closed SPORTS events (tag_id=1), filter to nfl single-game markets
mkts=[]  # (date, slug, cid)
tagc=Counter(); seriesc=Counter()
for off in range(0,3000,100):
    try: evs=http("%s/events?tag_id=1&closed=true&limit=100&offset=%d&order=endDate&ascending=false" % (GAMMA,off))
    except Exception as e: print("events off=%d ERR %s"%(off,str(e)[:50])); break
    if not evs: break
    for e in evs:
        if "nfl-" not in (e.get("slug","") or ""): continue
        for t in (e.get("tags") or []): tagc[(t.get("id"),t.get("label"))]+=1
        ser=e.get("series")
        if isinstance(ser,list):
            for s in ser: seriesc[(s.get("id"),s.get("slug"))]+=1
        for m in (e.get("markets") or []):
            ms=m.get("slug","") or ""; cid=m.get("conditionId") or m.get("condition_id")
            if cid and GAME.match(ms):
                d=ms[-10:]; mkts.append((d,ms,cid))
    if len(mkts)>=400: break
mkts.sort(reverse=True)
dates=[d for d,_,_ in mkts]
print("nfl single-game markets found:", len(mkts))
if dates: print("date range:", min(dates), "->", max(dates))
print("NFL-event tag frequency:", tagc.most_common(8))
print("NFL-event series frequency:", seriesc.most_common(5))
# bucket by season era
def era(d):
    if d>="2026-08": return "2026-preseason"
    if d>="2025-09" or (d>="2026-01" and d<"2026-03"): return "2025-regular/playoffs"
    return "older"
from collections import Counter as C2
print("markets by era:", dict(C2(era(d) for d,_,_ in mkts)))
# /trades viability: sample newest, a 2025-regular, and oldest
def sample_for(pred):
    for d,ms,cid in mkts:
        if pred(d): return (d,ms,cid)
    return None
tests=[]
s=sample_for(lambda d:d>="2026-08"); tests.append(("2026-preseason",s))
s=sample_for(lambda d:"2025-09"<=d<="2026-02"); tests.append(("2025-regular",s))
s=sample_for(lambda d:d<"2025-01"); tests.append(("older",s))
print("\n=== /trades viability across eras ===")
for label,s in tests:
    if not s: print("  %s: no market in window"%label); continue
    d,ms,cid=s
    try:
        tr=http("%s/trades?market=%s&limit=500"%(DATA,cid))
        n=len(tr) if isinstance(tr,list) else 0
        wals=len(set(t.get("proxyWallet") for t in tr if isinstance(t,dict) and t.get("proxyWallet"))) if isinstance(tr,list) else 0
        print("  %-16s %s (%s) -> trades=%d distinct_wallets=%d"%(label, ms, d, n, wals))
    except Exception as e: print("  %s %s ERR %s"%(label, ms, str(e)[:60]))
PY
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$enc = New-Object Text.UTF8Encoding($false)
$tf  = Join-Path $env:TEMP 'pk_nfld_chunk.sh'
$size = 20000; $first = $true
for ($i = 0; $i -lt $b64.Length; $i += $size) {
    $chunk = $b64.Substring($i, [Math]::Min($size, $b64.Length - $i))
    $op = if ($first) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pk_nfld.b64`n", $enc)
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $first = $false
}
[IO.File]::WriteAllText($tf, "base64 -d /tmp/pk_nfld.b64 | bash`n", $enc)
Write-Host "== NFL TAG + /trades WINDOW DIAGNOSTIC (READ-ONLY) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Remove-Item $tf -ErrorAction SilentlyContinue
