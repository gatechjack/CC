PY=/home/azureuser/trading_corp/venv/bin/python
ROOT=/home/azureuser/trading_corp
cd "$ROOT" || exit 2
export KEY_VAULT_URI=$(systemctl show trading-corp -p Environment | grep -oE 'KEY_VAULT_URI=[^ ]+' | head -1 | cut -d= -f2-)
echo "=== CONDITION 1: all SCALAR settlements in history + journal signed-net per (RO) ==="
PYTHONPATH=. "$PY" - <<'PY'
import asyncio, inspect, sqlite3
from trading_corp.utils import secrets as S
from trading_corp.prediction_markets.shard_snapshot_task import resolve_kalshi_keys
from trading_corp.brokers.kalshi import KalshiBroker
from trading_corp.prediction_markets import boot_reconcile as BR
ACCTS=[("kalshi_jack","KALSHI"),("kalshi_karen","kalshi_karen"),("kalshi_marc","kalshi_marc"),("kalshi_trey","kalshi_trey")]
sec=S.load_secrets()
pm=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True)
async def getj(c,p):
    r=c.get(p)
    if inspect.isawaitable(r): r=await r
    return r
scal=[]
async def scan(aid,ref):
    kid,pem=resolve_kalshi_keys(ref,sec)
    b=KalshiBroker(api_key_id=kid, private_key_pem=pem, demo=False); await b.connect()
    try:
        cur=""; pages=0
        while pages<25:
            raw=await getj(b._client,"/portfolio/settlements?limit=200"+("&cursor=%s"%cur if cur else ""))
            items=(raw or {}).get("settlements") or []
            for it in items:
                mr=str(it.get("market_result") or it.get("result") or "").strip().lower()
                if mr=="scalar":
                    scal.append((aid, it.get("ticker"), it.get("value"), it.get("revenue"), it.get("settled_time"), it.get("yes_count_fp"), it.get("no_count_fp")))
            cur=(raw or {}).get("cursor") or ""; pages+=1
            if not cur or not items: break
    finally:
        await b.disconnect()
def journal(aid,ticker):
    r=pm.execute("select sum(case when is_exit=0 and outcome_status='filled' then fill_count else 0 end) ent, sum(case when is_exit=1 and outcome_status='filled' then fill_count else 0 end) ex, sum(case when is_exit=1 then 1 else 0 end) nclose, group_concat(distinct coalesce(close_source,'NULL')) cs, group_concat(distinct outcome_leg) legs from pm_subdivision_order where account_id=? and ticker=? and dry_run=0",(aid,ticker)).fetchone()
    return r
async def main():
    for aid,ref in ACCTS: await scan(aid,ref)
    print("TOTAL scalar settlement records found:", len(scal))
    tickers=sorted(set(s[1] for s in scal))
    print("distinct scalar tickers:", tickers)
    for aid,tk,val,rev,st,yc,nc in scal:
        ent,ex,nclose,cs,legs=journal(aid,tk)
        net=(ent or 0)-(ex or 0)
        flag=" <<< OPEN POSITION -- STOP" if abs(net)>1e-9 else ""
        print("  %-14s %-32s value=%s rev=%s settled=%s yesct=%s noct=%s | journal: entered=%s exited=%s net_open=%s closes=%s src=%s legs=%s%s"%(
            aid,(tk or '')[:32],val,rev,st,yc,nc,ent or 0,ex or 0,net,nclose,cs,legs,flag))
    # any scalar ticker with a non-zero journal net on ANY account?
    openpos=[]
    for aid,tk,val,rev,st,yc,nc in scal:
        ent,ex,_,_,_=journal(aid,tk)
        if abs((ent or 0)-(ex or 0))>1e-9: openpos.append((aid,tk))
    print("OPEN scalar positions (net_open != 0):", openpos if openpos else "NONE -- all flat")
asyncio.run(main())
PY
echo "=== DONE c1scalars ==="
