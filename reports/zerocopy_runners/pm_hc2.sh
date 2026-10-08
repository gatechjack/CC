PY=/home/azureuser/trading_corp/venv/bin/python
ROOT=/home/azureuser/trading_corp
cd "$ROOT" || exit 2
export KEY_VAULT_URI=$(systemctl show trading-corp -p Environment | grep -oE 'KEY_VAULT_URI=[^ ]+' | head -1 | cut -d= -f2-)
echo "KEY_VAULT_URI present: ${KEY_VAULT_URI:+yes}"
echo "=== RECONCILE (RO compare, no latch) + venue settlements today ==="
PYTHONPATH=. "$PY" - <<'PY'
import asyncio, inspect, datetime
from trading_corp.utils import secrets as S
from trading_corp.prediction_markets.shard_snapshot_task import resolve_kalshi_keys
from trading_corp.brokers.kalshi import KalshiBroker
from trading_corp.prediction_markets import boot_reconcile as BR
import sqlite3
SINCE=int(datetime.datetime(2026,10,7,4,18,0,tzinfo=datetime.timezone.utc).timestamp())
ACCTS=[("kalshi_jack","KALSHI"),("kalshi_karen","kalshi_karen"),("kalshi_marc","kalshi_marc"),("kalshi_trey","kalshi_trey")]
sec=S.load_secrets()
pm=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True)
async def getj(c,p):
    r=c.get(p)
    if inspect.isawaitable(r): r=await r
    return r
async def one(aid,ref):
    kid,pem=resolve_kalshi_keys(ref,sec)
    b=KalshiBroker(api_key_id=kid, private_key_pem=pem, demo=False)
    await b.connect()
    try:
        c=b._client
        # reconcile
        j=BR.journal_signed_positions(pm,aid)
        last=None; poss=None
        for _ in range(3):
            try: poss=list(await c.portfolio.get_positions(fetch_all=True)); break
            except Exception as e: last=e; await asyncio.sleep(1.0)
        if poss is None:
            print("%-14s VENUE READ FAILED: %r (INCONCLUSIVE)"%(aid,last)); return
        k=BR.kalshi_signed_positions(poss); diffs=BR.compare(j,k)
        print("%-14s RECONCILE journal_tickers=%s kalshi_tickers=%s DIFFS=%s"%(aid,len(j),len(k),len(diffs)))
        for d in diffs: print("    DIFF %s j=%s k=%s [%s]"%(d.ticker,d.journal_signed,d.kalshi_signed,d.classification))
        # settlements since arm
        cur=""; pages=0; res={}; odd=[]
        while pages<8:
            raw=await getj(c,"/portfolio/settlements?limit=200"+("&cursor=%s"%cur if cur else ""))
            items=(raw or {}).get("settlements") or []
            stop=False
            for it in items:
                st=it.get("settled_time");
                try:
                    tt=BR_iso(st)
                except Exception:
                    tt=None
                mr=str(it.get("market_result") or it.get("result") or "").strip().lower()
                if tt is not None and tt>=SINCE:
                    res[mr]=res.get(mr,0)+1
                    if mr not in ("yes","no","void"):
                        odd.append((it.get("ticker"),mr,it.get("revenue"),st))
                elif tt is not None and tt<SINCE:
                    stop=True
            cur=(raw or {}).get("cursor") or ""; pages+=1
            if stop or not cur: break
        print("    settlements since arm by market_result:",res,"(scanned %d pages)"%pages)
        if odd:
            print("    *** NON-{yes,no,void} settlements today:")
            for t,mr,rev,st in odd: print("        ",t,mr,"rev",rev,st)
    finally:
        await b.disconnect()
def BR_iso(v):
    from trading_corp.prediction_markets.settlement import _iso_to_unix
    return _iso_to_unix(v)
async def main():
    for aid,ref in ACCTS: await one(aid,ref)
asyncio.run(main())
PY
echo "=== DONE hc2 ==="
