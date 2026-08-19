# pk_item2_verify_ro.ps1 -- READ-ONLY post-deploy verify for Item 2. NO writes.
# armed status, deployed + byte-locked LF-md5, mark ticks, and a DIRECT quote()
# proof on a currently-active MLB ticker (fix works even if no open position is
# quotable this moment). Run: powershell -ep bypass -f .\pk_item2_verify_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
EPID=$(systemctl show -p MainPID --value trading-corp 2>/dev/null)
echo "MainPID = $EPID"
if [ -n "$EPID" ] && [ "$EPID" != "0" ] && [ -r /proc/$EPID/environ ]; then
  KVLINE=$(tr '\0' '\n' < /proc/$EPID/environ | grep '^KEY_VAULT_URI=' | head -1)
  [ -n "$KVLINE" ] && export "$KVLINE"
fi
echo "=== deployed brokers/kalshi.py + 3 byte-locked files LF-md5 ==="
for f in trading_corp/brokers/kalshi.py trading_corp/agents/strategies/kalshi_copy_trader.py trading_corp/data/sports_team_mapping.py trading_corp/brokers/kalshi_live.py; do
  printf "%-58s " "$f"; tr -d '\r' < "$f" | md5sum | cut -d' ' -f1
done
echo "=== armed ==="
venv/bin/python3 - <<'PY'
import os, yaml
try:
    from dotenv import load_dotenv; load_dotenv("/home/azureuser/trading_corp/.env")
except Exception:
    pass
cfg = (yaml.safe_load(open("config/strategies.yaml")) or {}).get("poly_kalshi_mlb") or {}
auto = bool(cfg.get("auto_execute", False))
from trading_corp.persistence.models import StrategyState
DB = os.environ.get("TRADING_CORP_DB_URL", "sqlite:///data/trading_corp.db")
print("auto_execute =", auto, " dry_run =", (not auto),
      " halted =", StrategyState.from_persistence("poly_kalshi_mlb", db_url=DB).halted)
PY
echo "=== recent poly_kalshi mark ticks (marked>0 == Item 2 proof if an open pos is quotable) ==="
journalctl -u trading-corp --no-pager -o cat 2>/dev/null | grep "poly_kalshi mark tick" | tail -5
echo "=== DIRECT quote() proof on a currently-active MLB ticker ==="
venv/bin/python3 - <<'PY'
import asyncio, os
try:
    from dotenv import load_dotenv; load_dotenv("/home/azureuser/trading_corp/.env")
except Exception:
    pass
async def main():
    from pykalshi import MarketStatus
    from trading_corp.utils.secrets import load_secrets
    from trading_corp.brokers.kalshi_live import KalshiLiveBroker
    s = load_secrets()
    b = KalshiLiveBroker(api_key_id=s.kalshi_karen_api_key_id,
                         private_key_pem=s.kalshi_karen_private_key_pem,
                         demo=False, order_type="ioc", max_slippage_cents=2)
    await b.connect()
    c = b._read._client
    if c is None:
        print("STUB (creds missing)"); return
    ms = await c.get_markets(series_ticker="KXMLBGAME", status=MarketStatus.OPEN, limit=8)
    hit = None
    for m in (ms or []):
        t = getattr(m, "ticker", "") or ""
        if not t:
            continue
        q = await b._read.quote(t)
        print("quote(%s) = %s" % (t, q))
        if q and q > 0:
            hit = (t, q); break
    if hit:
        print("PROOF: quote() returns a real mid %.4f on %s -> Item 2 fix WORKS (was 0.0)" % (hit[1], hit[0]))
    else:
        print("no OPEN market returned >0 (pre-game empty books this moment); fix still installed per md5")
    await b.disconnect()
asyncio.run(main())
PY
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$sh = "printf %s '$b64' | base64 -d | bash"
Write-Host "== ITEM 2 POST-DEPLOY VERIFY (READ-ONLY) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts $sh --query "value[0].message" -o tsv
