# pk_fed_diag_ro.ps1 -- READ-ONLY: Kalshi Fed series + enumerate Poly per-meeting Fed events (tag_id=100196),
# classify per-meeting-band vs cumulative-by-date, count FOMC meetings + date range, /trades test. NO writes.
# Run: powershell -ep bypass -f .\pk_fed_diag_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
EPID=$(systemctl show -p MainPID --value trading-corp 2>/dev/null)
if [ -n "$EPID" ] && [ "$EPID" != "0" ] && [ -r /proc/$EPID/environ ]; then
  KVLINE=$(tr '\0' '\n' < /proc/$EPID/environ | grep '^KEY_VAULT_URI=' | head -1); [ -n "$KVLINE" ] && export "$KVLINE"
fi
echo "===== KALSHI FED series ====="
venv/bin/python3 - <<'PY'
import os, asyncio
try:
    from dotenv import load_dotenv; load_dotenv("/home/azureuser/trading_corp/.env")
except Exception: pass
async def main():
    from pykalshi import MarketStatus
    from trading_corp.utils.secrets import load_secrets
    from trading_corp.brokers.kalshi_live import KalshiLiveBroker
    s=load_secrets()
    b=KalshiLiveBroker(api_key_id=s.kalshi_karen_api_key_id, private_key_pem=s.kalshi_karen_private_key_pem, demo=False, order_type="ioc", max_slippage_cents=2)
    await b.connect(); c=b._read._client
    for series in ("KXFED","KXFEDDECISION","KXFEDFUNDS","KXFOMC","KXFEDCUT","KXRATECUTS","KXFEDRATE"):
        got=False
        for st in (MarketStatus.OPEN, MarketStatus.SETTLED):
            try:
                ms=await c.get_markets(series_ticker=series, status=st, limit=4); ms=ms or []
                if ms:
                    got=True; print("  %s %s -> %d" % (series, st, len(ms)))
                    for m in ms[:4]: print("     ", getattr(m,"ticker","")[:42], "|", (getattr(m,"title","") or "")[:58])
            except Exception as e: pass
        if not got: print("  %s -> (none)" % series)
    await b.disconnect()
asyncio.run(main())
PY
echo ""
echo "===== POLYMARKET per-meeting Fed events (tag_id=100196) ====="
venv/bin/python3 - <<'PY'
import json, urllib.request, re
def http(u,t=30):
    r=urllib.request.Request(u, headers={"User-Agent":"research/1.0"})
    with urllib.request.urlopen(r,timeout=t) as x: return json.loads(x.read().decode())
GAMMA="https://gamma-api.polymarket.com"; DATA="https://data-api.polymarket.com"
def kind(mslugs):
    j=" ".join(mslugs)
    if "-by-" in " ".join(m for m in mslugs if m.startswith("fed-rate-cut-by")) or all(m.startswith("fed-rate-cut-by") for m in mslugs if m):
        return "cumulative-by-date"
    if "dissent" in j: return "dissent-granular"
    if ("bps-after" in j or ("no-change" in j and "meeting" in j) or "bps" in j and "meeting" in j): return "per-meeting-band"
    return "other"
permeeting=[]; allcids=[]
for off in range(0,600,100):
    try: evs=http("%s/events?tag_id=100196&closed=true&limit=100&offset=%d&order=endDate&ascending=false"%(GAMMA,off))
    except Exception as e: print("  events off=%d ERR %s"%(off,str(e)[:50])); break
    if not evs: break
    for e in evs:
        eslug=e.get("slug","") or ""; mks=[(m.get("slug","") or "", m.get("conditionId") or m.get("condition_id")) for m in (e.get("markets") or [])]
        k=kind([s for s,_ in mks])
        end=(e.get("endDate","") or "")[:10]
        if k=="per-meeting-band":
            permeeting.append((end, eslug, len(mks)))
            for s,cid in mks:
                if cid: allcids.append((end,s,cid))
print("  per-meeting-band events found:", len(permeeting))
for end,eslug,nm in permeeting[:16]: print("   ", end, eslug, "(%d outcomes)"%nm)
ends=sorted(set(e for e,_,_ in permeeting))
print("  distinct meeting-events date range:", (ends[0] if ends else "n/a"), "->", (ends[-1] if ends else "n/a"), " count=", len(ends))
# /trades test on a recent per-meeting market
if allcids:
    allcids.sort(reverse=True); end,s,cid=allcids[0]
    try:
        tr=http("%s/trades?market=%s&limit=500"%(DATA,cid)); n=len(tr) if isinstance(tr,list) else 0
        w=len(set(t.get("proxyWallet") for t in tr if isinstance(t,dict) and t.get("proxyWallet"))) if isinstance(tr,list) else 0
        print("  /trades %s (%s) -> trades=%d wallets=%d"%(s[:50],end,n,w))
    except Exception as e: print("  /trades ERR",str(e)[:50])
PY
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$enc = New-Object Text.UTF8Encoding($false)
$tf  = Join-Path $env:TEMP 'pk_fedd_chunk.sh'
$size = 20000; $first = $true
for ($i = 0; $i -lt $b64.Length; $i += $size) {
    $chunk = $b64.Substring($i, [Math]::Min($size, $b64.Length - $i))
    $op = if ($first) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pk_fedd.b64`n", $enc)
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $first = $false
}
[IO.File]::WriteAllText($tf, "base64 -d /tmp/pk_fedd.b64 | bash`n", $enc)
Write-Host "== FED KALSHI-SERIES + POLY per-meeting ENUMERATION (READ-ONLY) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Remove-Item $tf -ErrorAction SilentlyContinue
