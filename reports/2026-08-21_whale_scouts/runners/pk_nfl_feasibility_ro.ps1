# pk_nfl_feasibility_ro.ps1 -- READ-ONLY NFL-copy feasibility probe. NO writes/orders.
# (1) Kalshi lists NFL single-game moneyline (KXNFLGAME)? (2) Polymarket NFL tag_id + single-game slug
# pattern (Gamma). (3) per-market trader discovery works. Run: powershell -ep bypass -f .\pk_nfl_feasibility_ro.ps1
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
def http(url, timeout=25):
    req=urllib.request.Request(url, headers={"User-Agent":"research/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read().decode())
async def main():
    print("===== 1. KALSHI NFL MARKETS =====")
    try:
        from pykalshi import MarketStatus
        from trading_corp.utils.secrets import load_secrets
        from trading_corp.brokers.kalshi_live import KalshiLiveBroker
        s=load_secrets()
        b=KalshiLiveBroker(api_key_id=s.kalshi_karen_api_key_id, private_key_pem=s.kalshi_karen_private_key_pem, demo=False, order_type="ioc", max_slippage_cents=2)
        await b.connect(); c=b._read._client
        for series in ("KXNFLGAME","KXNFL","KXNFLSB","KXNFLWINS"):
            for st in (MarketStatus.OPEN, MarketStatus.SETTLED):
                try:
                    ms=await c.get_markets(series_ticker=series, status=st, limit=6); ms=ms or []
                    if ms:
                        print("  series=%s %s -> %d" % (series, st, len(ms)))
                        for m in ms[:4]: print("     ", getattr(m,"ticker","")[:46], "|", (getattr(m,"title","") or "")[:56])
                except Exception as e: print("  series=%s %s ERR %s" % (series, st, str(e)[:70]))
        await b.disconnect()
    except Exception as e: print("  KALSHI section failed:", str(e)[:150])

    print("\n===== 2. POLYMARKET NFL tag_id + single-game slug pattern =====")
    sample=None
    try:
        d=http("https://gamma-api.polymarket.com/public-search?q=NFL&limit_per_type=20")
        evs=(d or {}).get("events",[]) if isinstance(d,dict) else []
        print("  public-search NFL events:", len(evs))
        for e in evs[:14]:
            tags=[(t.get("id"),t.get("label")) for t in (e.get("tags") or [])]
            print("   EVENT slug=%s tags=%s" % (e.get("slug",""), tags[:4]))
            for m in (e.get("markets") or [])[:2]:
                cid=m.get("conditionId") or m.get("condition_id")
                print("      market slug=%s cond=%s" % (m.get("slug",""), (cid or "")[:14]))
                if sample is None and cid and "nfl-" in (m.get("slug","") or ""): sample=(m.get("slug"),cid)
    except Exception as e: print("  gamma public-search failed:", str(e)[:120])

    print("\n===== 3. per-market trader discovery test =====")
    if sample:
        slug,cid=sample; print("  probing", slug, cid[:16])
        try:
            d=http("https://data-api.polymarket.com/trades?market=%s&limit=6" % cid)
            print("   trades -> type=%s n=%s" % (type(d).__name__, len(d) if isinstance(d,list) else "?"))
            for t in (d[:3] if isinstance(d,list) else []):
                print("      wallet=%s side=%s size=%s price=%s" % (t.get("proxyWallet"), t.get("side"), t.get("size"), t.get("price")))
        except Exception as e: print("   trades ERR", str(e)[:90])
    else:
        print("  no nfl- market condition_id captured")
    print("\nDONE (read-only).")
asyncio.run(main())
PY
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$enc = New-Object Text.UTF8Encoding($false)
$tf  = Join-Path $env:TEMP 'pk_nflf_chunk.sh'
$size = 20000; $first = $true
for ($i = 0; $i -lt $b64.Length; $i += $size) {
    $chunk = $b64.Substring($i, [Math]::Min($size, $b64.Length - $i))
    $op = if ($first) { '>' } else { '>>' }
    [IO.File]::WriteAllText($tf, "printf %s '$chunk' $op /tmp/pk_nflf.b64`n", $enc)
    az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv | Out-Null
    $first = $false
}
[IO.File]::WriteAllText($tf, "base64 -d /tmp/pk_nflf.b64 | bash`n", $enc)
Write-Host "== NFL COPY FEASIBILITY PROBE (READ-ONLY) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
Remove-Item $tf -ErrorAction SilentlyContinue
