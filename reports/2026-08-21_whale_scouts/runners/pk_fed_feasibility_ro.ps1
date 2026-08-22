# pk_fed_feasibility_ro.ps1 -- READ-ONLY Fed-rates feasibility. NO writes. Find Kalshi Fed series +
# Polymarket Fed tag/slug + MARKET STRUCTURE (per-meeting bands vs count) for resolution-alignment.
# Run: powershell -ep bypass -f .\pk_fed_feasibility_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
EPID=$(systemctl show -p MainPID --value trading-corp 2>/dev/null)
if [ -n "$EPID" ] && [ "$EPID" != "0" ] && [ -r /proc/$EPID/environ ]; then
  KVLINE=$(tr '\0' '\n' < /proc/$EPID/environ | grep '^KEY_VAULT_URI=' | head -1); [ -n "$KVLINE" ] && export "$KVLINE"
fi
venv/bin/python3 - <<'PY'
import os, asyncio, json, urllib.request
try:
    from dotenv import load_dotenv; load_dotenv("/home/azureuser/trading_corp/.env")
except Exception: pass
def http(url, timeout=30):
    req=urllib.request.Request(url, headers={"User-Agent":"research/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read().decode())
GAMMA="https://gamma-api.polymarket.com"; DATA="https://data-api.polymarket.com"
async def main():
    print("===== 1. KALSHI FED series (guesses + structure) =====")
    try:
        from pykalshi import MarketStatus
        from trading_corp.utils.secrets import load_secrets
        from trading_corp.brokers.kalshi_live import KalshiLiveBroker
        s=load_secrets()
        b=KalshiLiveBroker(api_key_id=s.kalshi_karen_api_key_id, private_key_pem=s.kalshi_karen_private_key_pem, demo=False, order_type="ioc", max_slippage_cents=2)
        await b.connect(); c=b._read._client
        for series in ("KXFED","KXFEDDECISION","KXFEDFUNDS","KXFOMC","KXRATEHIKE","KXRATECUTS","KXFEDCUT"):
            for st in (MarketStatus.OPEN, MarketStatus.SETTLED):
                try:
                    ms=await c.get_markets(series_ticker=series, status=st, limit=6); ms=ms or []
                    if ms:
                        print("  series=%s %s -> %d" % (series, st, len(ms)))
                        for m in ms[:5]: print("     ", getattr(m,"ticker","")[:44], "|", (getattr(m,"title","") or "")[:60])
                except Exception as e: print("  series=%s %s ERR %s"%(series,st,str(e)[:55]))
        await b.disconnect()
    except Exception as e: print("  KALSHI failed:", str(e)[:150])

    print("\n===== 2. POLYMARKET FED events (tag + structure) =====")
    for q in ("Fed decision","FOMC","fed interest rate","fed rate cut September"):
        try:
            d=http("%s/public-search?q=%s&limit_per_type=8" % (GAMMA, urllib.parse.quote(q)))
        except Exception as e:
            print("  q=%s ERR %s"%(q,str(e)[:50])); continue
        evs=(d or {}).get("events",[]) if isinstance(d,dict) else []
        print("  q='%s' -> %d events" % (q, len(evs)))
        for e in evs[:5]:
            tags=[(t.get("id"),t.get("label")) for t in (e.get("tags") or [])]
            mks=[m.get("slug","") for m in (e.get("markets") or [])]
            print("    EVENT slug=%s closed=%s tags=%s" % (e.get("slug",""), e.get("closed"), tags[:4]))
            print("       markets(%d): %s" % (len(mks), mks[:6]))
    print("\nDONE (read-only).")
import urllib.parse
asyncio.run(main())
PY
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$enc = New-Object Text.UTF8Encoding($false)
$tf  = Join-Path $env:TEMP 'pk_fedf_chunk.sh'
$size = 20000; $first = $true
for ($i = 0; $i -lt $b64.Length; $i += $size) {
    $chunk = $b64.Substring($i, [Math]::Min($size, $b64.Length - $i))
    $op = if ($first) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pk_fedf.b64`n", $enc)
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $first = $false
}
[IO.File]::WriteAllText($tf, "base64 -d /tmp/pk_fedf.b64 | bash`n", $enc)
Write-Host "== FED-RATES FEASIBILITY PROBE (READ-ONLY) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Remove-Item $tf -ErrorAction SilentlyContinue
