# pk_ufc_feasibility_ro.ps1 -- READ-ONLY UFC-copy feasibility probe. NO writes/orders.
# (1) Kalshi still lists UFC single fights (KXUFCFIGHT) + title/futures? (2) Polymarket UFC market
# slug pattern (Gamma). (3) Can we DISCOVER UFC whales -- does data-api expose per-market traders?
# Run: powershell -ep bypass -f .\pk_ufc_feasibility_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
EPID=$(systemctl show -p MainPID --value trading-corp 2>/dev/null)
if [ -n "$EPID" ] && [ "$EPID" != "0" ] && [ -r /proc/$EPID/environ ]; then
  KVLINE=$(tr '\0' '\n' < /proc/$EPID/environ | grep '^KEY_VAULT_URI=' | head -1)
  [ -n "$KVLINE" ] && export "$KVLINE"
fi
venv/bin/python3 - <<'PY'
import os, asyncio, json, urllib.request, urllib.parse
try:
    from dotenv import load_dotenv; load_dotenv("/home/azureuser/trading_corp/.env")
except Exception: pass

def http(url, timeout=25):
    req=urllib.request.Request(url, headers={"User-Agent":"research/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

async def main():
    # ===== 1. KALSHI UFC targets =====
    print("===== 1. KALSHI UFC MARKETS =====")
    try:
        from pykalshi import MarketStatus
        from trading_corp.utils.secrets import load_secrets
        from trading_corp.brokers.kalshi_live import KalshiLiveBroker
        s=load_secrets()
        b=KalshiLiveBroker(api_key_id=s.kalshi_karen_api_key_id, private_key_pem=s.kalshi_karen_private_key_pem,
                           demo=False, order_type="ioc", max_slippage_cents=2)
        await b.connect(); c=b._read._client
        if c is None:
            print("  kalshi STUB (creds missing)")
        else:
            for series in ("KXUFCFIGHT","KXUFC","KXUFCWELTERWEIGHTTITLE","KXUFCLIGHTWEIGHTTITLE","KXUFCCHAMP"):
                for st in (MarketStatus.OPEN, MarketStatus.SETTLED):
                    try:
                        ms=await c.get_markets(series_ticker=series, status=st, limit=6)
                        ms=ms or []
                        if ms:
                            print("  series=%s %s -> %d markets" % (series, st, len(ms)))
                            for m in ms[:4]:
                                print("     ", getattr(m,"ticker","")[:48], "|", (getattr(m,"title","") or "")[:60])
                    except Exception as e:
                        print("  series=%s %s ERR %s" % (series, st, str(e)[:80]))
        await b.disconnect()
    except Exception as e:
        print("  KALSHI section failed:", str(e)[:160])

    # ===== 2. POLYMARKET UFC market slug pattern (Gamma public search) =====
    print("\n===== 2. POLYMARKET UFC MARKET SLUGS (gamma) =====")
    ufc_slugs=[]; sample_market=None
    try:
        data=http("https://gamma-api.polymarket.com/public-search?q=UFC&limit_per_type=20")
        evs=(data or {}).get("events", []) if isinstance(data, dict) else []
        print("  public-search events matched:", len(evs))
        for e in evs[:12]:
            eslug=e.get("slug",""); print("   EVENT slug=%s | %s" % (eslug, (e.get("title","") or "")[:50]))
            for m in (e.get("markets") or [])[:2]:
                ms_slug=m.get("slug",""); cid=m.get("conditionId") or m.get("condition_id")
                ufc_slugs.append(ms_slug)
                if sample_market is None and cid: sample_market=(ms_slug, cid)
                print("      market slug=%s cond=%s" % (ms_slug, (cid or "")[:14]))
    except Exception as e:
        print("  gamma public-search failed:", str(e)[:120])
    # fallback: events endpoint grep
    if not ufc_slugs:
        try:
            evs=http("https://gamma-api.polymarket.com/events?closed=false&active=true&limit=400")
            hits=[e for e in (evs or []) if "ufc" in (e.get("slug","")+e.get("title","")).lower()]
            print("  events-endpoint UFC hits:", len(hits))
            for e in hits[:8]:
                print("   EVENT slug=%s" % e.get("slug",""))
                for m in (e.get("markets") or [])[:2]:
                    print("      market slug=%s" % m.get("slug",""))
        except Exception as e:
            print("  gamma events failed:", str(e)[:120])

    # ===== 3. WHALE DISCOVERY feasibility: does data-api expose per-market traders? =====
    print("\n===== 3. WHALE DISCOVERY (data-api per-market traders) =====")
    if sample_market:
        slug,cid=sample_market
        print("  probing market slug=%s cond=%s" % (slug, cid[:16]))
        for label,url in (("trades?market=cond","https://data-api.polymarket.com/trades?market=%s&limit=8" % cid),
                          ("holders?market=cond","https://data-api.polymarket.com/holders?market=%s&limit=8" % cid)):
            try:
                d=http(url)
                n=len(d) if isinstance(d,list) else len((d or {}).get("holders",[]) if isinstance(d,dict) else [])
                print("   %s -> type=%s n=%s" % (label, type(d).__name__, n))
                sample=d[:2] if isinstance(d,list) else d
                wallets=set()
                for row in (d if isinstance(d,list) else []):
                    for k in ("proxyWallet","user","maker","taker","wallet","account"):
                        if isinstance(row,dict) and row.get(k): wallets.add(row[k])
                print("      distinct wallet-like fields in first rows:", list(wallets)[:4] or "(none found)")
            except Exception as e:
                print("   %s ERR %s" % (label, str(e)[:100]))
    else:
        print("  no sample UFC market condition_id captured -> can't test per-market traders this run")
    print("\nDONE (read-only).")

asyncio.run(main())
PY
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$enc = New-Object Text.UTF8Encoding($false)
$tf  = Join-Path $env:TEMP 'pk_ufc_chunk.sh'
$size = 20000; $first = $true
for ($i = 0; $i -lt $b64.Length; $i += $size) {
    $chunk = $b64.Substring($i, [Math]::Min($size, $b64.Length - $i))
    $op = if ($first) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pk_ufc.b64`n", $enc)
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $first = $false
}
[IO.File]::WriteAllText($tf, "base64 -d /tmp/pk_ufc.b64 | bash`n", $enc)
Write-Host "== UFC COPY FEASIBILITY PROBE (READ-ONLY) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Remove-Item $tf -ErrorAction SilentlyContinue
