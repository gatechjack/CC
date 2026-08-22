# pk_nba_feasibility_ro.ps1 -- READ-ONLY NBA-copy feasibility + enumeration handle. NO writes.
# Kalshi KXNBAGAME single-game (disambiguate KXNBAGAMES/KXNBAGAME7); Poly NBA series_id/tag_id + slug +
# date range + /trades test. Run: powershell -ep bypass -f .\pk_nba_feasibility_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
EPID=$(systemctl show -p MainPID --value trading-corp 2>/dev/null)
if [ -n "$EPID" ] && [ "$EPID" != "0" ] && [ -r /proc/$EPID/environ ]; then
  KVLINE=$(tr '\0' '\n' < /proc/$EPID/environ | grep '^KEY_VAULT_URI=' | head -1); [ -n "$KVLINE" ] && export "$KVLINE"
fi
venv/bin/python3 - <<'PY'
import os, asyncio, json, urllib.request, re
try:
    from dotenv import load_dotenv; load_dotenv("/home/azureuser/trading_corp/.env")
except Exception: pass
def http(url, timeout=30):
    req=urllib.request.Request(url, headers={"User-Agent":"research/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read().decode())
GAMMA="https://gamma-api.polymarket.com"; DATA="https://data-api.polymarket.com"
GAME=re.compile(r"^nba-[a-z0-9]+-[a-z0-9]+-\d{4}-\d{2}-\d{2}$")
async def main():
    print("===== 1. KALSHI NBA (collision check) =====")
    try:
        from pykalshi import MarketStatus
        from trading_corp.utils.secrets import load_secrets
        from trading_corp.brokers.kalshi_live import KalshiLiveBroker
        s=load_secrets()
        b=KalshiLiveBroker(api_key_id=s.kalshi_karen_api_key_id, private_key_pem=s.kalshi_karen_private_key_pem, demo=False, order_type="ioc", max_slippage_cents=2)
        await b.connect(); c=b._read._client
        for series in ("KXNBAGAME","KXNBAGAMES","KXNBAGAME7"):
            for st in (MarketStatus.OPEN, MarketStatus.SETTLED):
                try:
                    ms=await c.get_markets(series_ticker=series, status=st, limit=5); ms=ms or []
                    if ms:
                        print("  series=%s %s -> %d" % (series, st, len(ms)))
                        for m in ms[:3]: print("     ", getattr(m,"ticker","")[:46], "|", (getattr(m,"title","") or "")[:52])
                except Exception as e: print("  series=%s %s ERR %s"%(series,st,str(e)[:60]))
        await b.disconnect()
    except Exception as e: print("  KALSHI failed:", str(e)[:150])

    print("\n===== 2. POLYMARKET NBA series/tag + slug =====")
    series_ids=set()
    try:
        d=http("%s/public-search?q=NBA&limit_per_type=20" % GAMMA)
        evs=(d or {}).get("events",[]) if isinstance(d,dict) else []
        print("  public-search NBA events:", len(evs))
        for e in evs[:14]:
            tags=[(t.get("id"),t.get("label")) for t in (e.get("tags") or [])]
            ser=e.get("series"); sers=[(x.get("id"),x.get("slug")) for x in ser] if isinstance(ser,list) else ser
            if isinstance(ser,list):
                for x in ser:
                    if x.get("id"): series_ids.add(str(x.get("id")))
            mk=[(m.get("slug"), (m.get("conditionId") or "")[:12]) for m in (e.get("markets") or [])][:2]
            print("   EVENT slug=%s tags=%s series=%s mkts=%s" % (e.get("slug",""), tags[:3], sers, mk))
    except Exception as e: print("  public-search failed:", str(e)[:120])

    print("\n===== 3. enumerate by series_id -> date range + /trades =====")
    for sid in list(series_ids)[:4]:
        try:
            evs=http("%s/events?series_id=%s&closed=true&limit=100&order=endDate&ascending=false" % (GAMMA, sid))
        except Exception as e:
            print("  series_id=%s ERR %s"%(sid,str(e)[:50])); continue
        games=[]
        for e in (evs or []):
            for m in (e.get("markets") or []):
                ms=m.get("slug","") or ""; cid=m.get("conditionId") or m.get("condition_id")
                if cid and GAME.match(ms): games.append((ms[-10:],ms,cid))
        games.sort(reverse=True)
        dr=("%s..%s"%(games[-1][0],games[0][0])) if games else "none"
        print("  series_id=%s closed_events=%d nba-single-games=%d date_range=%s"%(sid, len(evs or []), len(games), dr))
        if games:
            d,ms,cid=games[0]
            try:
                tr=http("%s/trades?market=%s&limit=500"%(DATA,cid))
                n=len(tr) if isinstance(tr,list) else 0
                w=len(set(t.get("proxyWallet") for t in tr if isinstance(t,dict) and t.get("proxyWallet"))) if isinstance(tr,list) else 0
                print("     /trades %s (%s) -> trades=%d wallets=%d"%(ms,d,n,w))
            except Exception as e: print("     /trades ERR",str(e)[:50])
    print("\nDONE (read-only).")
asyncio.run(main())
PY
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$enc = New-Object Text.UTF8Encoding($false)
$tf  = Join-Path $env:TEMP 'pk_nbaf_chunk.sh'
$size = 20000; $first = $true
for ($i = 0; $i -lt $b64.Length; $i += $size) {
    $chunk = $b64.Substring($i, [Math]::Min($size, $b64.Length - $i))
    $op = if ($first) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pk_nbaf.b64`n", $enc)
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $first = $false
}
[IO.File]::WriteAllText($tf, "base64 -d /tmp/pk_nbaf.b64 | bash`n", $enc)
Write-Host "== NBA COPY FEASIBILITY + ENUMERATION PROBE (READ-ONLY) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Remove-Item $tf -ErrorAction SilentlyContinue
