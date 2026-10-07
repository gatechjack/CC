PY=/home/azureuser/trading_corp/venv/bin/python
cd /home/azureuser/trading_corp || exit 2
export KEY_VAULT_URI=$(systemctl show trading-corp -p Environment | grep -oE 'KEY_VAULT_URI=[^ ]+' | head -1 | cut -d= -f2-)
echo "KEY_VAULT_URI present: ${KEY_VAULT_URI:+yes}"
PYTHONPATH=. "$PY" - <<'PY'
import asyncio, inspect, sqlite3
from trading_corp.utils import secrets as S
from trading_corp.prediction_markets.shard_snapshot_task import resolve_kalshi_keys
from trading_corp.brokers.kalshi import KalshiBroker
TICKER="KXUFCFIGHT-26SEP29BULVIS-VIS"
ACCTS=[("kalshi_jack","KALSHI"),("kalshi_karen","kalshi_karen"),("kalshi_marc","kalshi_marc"),("kalshi_trey","kalshi_trey")]
sec=S.load_secrets()
pm=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True)
cb={}
for r in pm.execute("select account_id, sum(case when is_exit=0 and outcome_status='filled' then fill_count else 0 end), sum(case when is_exit=0 and outcome_status='filled' then fill_count*fill_price else 0 end) from pm_subdivision_order where ticker=? and dry_run=0 group by account_id",(TICKER,)):
    cb[r[0]]=(r[1] or 0.0, r[2] or 0.0)

async def getj(c, path):
    r=c.get(path)
    if inspect.isawaitable(r): r=await r
    return r

async def market_lookup(c):
    try:
        raw=await getj(c,"/markets/%s"%TICKER)
        m=(raw or {}).get("market") or raw or {}
        print("MARKET result=%r status=%r result_val/settlement=%r/%r expiration=%r"%(
            m.get("result"), m.get("status"), m.get("settlement_value"), m.get("settlement_value_dollars"), m.get("expiration_time")))
        print("   market keys:", sorted(m.keys()))
    except Exception as e:
        print("MARKET lookup ERR: %r"%e)

async def one(aid, ref, do_market):
    kid,pem=resolve_kalshi_keys(ref,sec)
    print("---- %s (ref=%s) keys_resolved=%s ----"%(aid,ref,bool(kid and pem)))
    if not (kid and pem): return
    b=KalshiBroker(api_key_id=kid, private_key_pem=pem, demo=False)
    await b.connect()
    try:
        c=b._client
        if do_market: await market_lookup(c)
        try:
            poss=await c.portfolio.get_positions(fetch_all=True)
            tp=[p for p in poss if str(getattr(p,'ticker','')).upper()==TICKER]
            print("%s positions: total=%s ticker_present=%s position_fp=%s"%(aid,len(poss),bool(tp),[getattr(p,'position_fp',None) for p in tp]))
        except Exception as e:
            print("%s positions ERR: %r"%(aid,e))
        found=None; cur=""; pages=0
        while pages<6:
            path="/portfolio/settlements?limit=200"+("&cursor=%s"%cur if cur else "")
            try: raw=await getj(c,path)
            except Exception as e:
                print("%s settlements page %s ERR: %r"%(aid,pages,e)); break
            items=(raw or {}).get("settlements") or []
            for it in items:
                if str(it.get("ticker","")).upper()==TICKER: found=it; break
            if found: break
            cur=(raw or {}).get("cursor") or ""; pages+=1
            if not cur: break
        if found:
            print("%s SETTLEMENT market_result=%r revenue=%r settled_time=%r yes_count=%r no_count=%r"%(
                aid, found.get("market_result") or found.get("result"), found.get("revenue"),
                found.get("settled_time"), found.get("yes_count"), found.get("no_count")))
            print("     settlement keys:", sorted(found.keys()))
        else:
            print("%s SETTLEMENT: ticker not found in %s settlement pages scanned"%(aid,pages))
        ent,cost=cb.get(aid,(0,0))
        print("     journal cost_basis=$%.4f entered=%s"%(cost,ent))
    finally:
        await b.disconnect()

async def main():
    first=True
    for aid,ref in ACCTS:
        await one(aid,ref,first); first=False
asyncio.run(main())
PY
echo "=== DONE p1b ==="
