PY=/home/azureuser/trading_corp/venv/bin/python
ROOT=/home/azureuser/trading_corp
cd "$ROOT" || exit 2
export KEY_VAULT_URI=$(systemctl show trading-corp -p Environment | grep -oE 'KEY_VAULT_URI=[^ ]+' | head -1 | cut -d= -f2-)
echo "=== TIME + restart-window check ==="
"$PY" - <<'PY'
import datetime
now=datetime.datetime.now(datetime.timezone.utc)
# EDT = UTC-4 (Oct, before Nov 2). session-hold window = 13:15Z..20:00Z (09:15-16:00 ET).
hm=now.hour*60+now.minute
hold = (13*60+15) <= hm < (20*60)
et=now - datetime.timedelta(hours=4)
print("NOW %sZ  (~%s ET)  -> restart window: %s"%(now.strftime('%Y-%m-%d %H:%M'), et.strftime('%H:%M'),
      "HOLD (in/at session 09:15-16:00 ET)" if hold else "OK (pre-market / after-close)"))
PY
echo "=== 3.1 RECONCILE (RO compare, no latch) -- DIFFS must be 0 on all four ==="
PYTHONPATH=. "$PY" - <<'PY'
import asyncio, sqlite3
from trading_corp.utils import secrets as S
from trading_corp.prediction_markets.shard_snapshot_task import resolve_kalshi_keys
from trading_corp.brokers.kalshi import KalshiBroker
from trading_corp.prediction_markets import boot_reconcile as BR
ACCTS=[("kalshi_jack","KALSHI"),("kalshi_karen","kalshi_karen"),("kalshi_marc","kalshi_marc"),("kalshi_trey","kalshi_trey")]
sec=S.load_secrets()
pm=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True)
async def gp(ref):
    kid,pem=resolve_kalshi_keys(ref,sec)
    b=KalshiBroker(api_key_id=kid, private_key_pem=pem, demo=False); await b.connect()
    try:
        last=None
        for _ in range(3):
            try: return list(await b._client.portfolio.get_positions(fetch_all=True))
            except Exception as e: last=e; await asyncio.sleep(1.0)
        raise last
    finally: await b.disconnect()
async def main():
    allclean=True
    for aid,ref in ACCTS:
        j=BR.journal_signed_positions(pm,aid)
        try: poss=await gp(ref)
        except Exception as e:
            print("%-14s VENUE READ FAILED %r -> INCONCLUSIVE (STOP)"%(aid,e)); allclean=False; continue
        k=BR.kalshi_signed_positions(poss); d=BR.compare(j,k)
        print("%-14s journal=%s venue=%s DIFFS=%s"%(aid,len(j),len(k),len(d)))
        for x in d: print("    DIFF %s j=%s k=%s [%s]"%(x.ticker,x.journal_signed,x.kalshi_signed,x.classification))
        if d: allclean=False
    print("RECONCILE_ALL_CLEAN:", allclean, "-> APPLY GATE:", "OPEN" if allclean else "CLOSED (STOP, apply nothing)")
asyncio.run(main())
PY
echo "=== box settlement.py CR-sha (expect BASE a1abe0e3b04a85e4) ==="
tr -d '\r' < "$ROOT/trading_corp/prediction_markets/settlement.py" | sha256sum | cut -c1-16
echo "=== DONE p3recon ==="
