PY=/home/azureuser/trading_corp/venv/bin/python
ROOT=/home/azureuser/trading_corp
cd "$ROOT" || exit 2
export KEY_VAULT_URI=$(systemctl show trading-corp -p Environment | grep -oE 'KEY_VAULT_URI=[^ ]+' | head -1 | cut -d= -f2-)
TICK="KXCS2GAME-26OCT070800M80TS-TS"
echo "=== A: journal rows for $TICK (jack/cs2) -- full detail (RO) ==="
"$PY" - "$TICK" <<'PY'
import sqlite3, sys, datetime
T=sys.argv[1]
pm=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True)
pm.row_factory=sqlite3.Row
def fmt(ts):
    try: return datetime.datetime.utcfromtimestamp(int(ts)).strftime('%Y-%m-%d %H:%M:%SZ')
    except Exception: return str(ts)
for r in pm.execute("select id,account_id,category,wallet,outcome_leg,outcome_status,fill_count,fill_price,submitted_price,condition_id,outcome_index,signal_outcome,signal_slug,leg_audit,coalesce(response_ts,submitted_ts) ts,error_detail from pm_subdivision_order where ticker=? order by id",(T,)):
    print("id=%s %s/%s wallet=%s leg=%s status=%s fill=%s px=%s subpx=%s oidx=%s ts=%s"%(r["id"],r["account_id"],r["category"],r["wallet"][:14],r["outcome_leg"],r["outcome_status"],r["fill_count"],r["fill_price"],r["submitted_price"],r["outcome_index"],fmt(r["ts"])))
    print("    signal_outcome=%r signal_slug=%r"%(r["signal_outcome"],r["signal_slug"]))
    print("    leg_audit=%r"%(r["leg_audit"],))
    print("    cid=%s err=%r"%(r["condition_id"],r["error_detail"]))
PY
echo "=== B: authenticated Kalshi market lookup (RO GET /markets/TICK) -- side names ==="
PYTHONPATH=. "$PY" - "$TICK" <<'PY'
import asyncio, inspect, sys
from trading_corp.utils import secrets as S
from trading_corp.prediction_markets.shard_snapshot_task import resolve_kalshi_keys
from trading_corp.brokers.kalshi import KalshiBroker
T=sys.argv[1]; sec=S.load_secrets()
async def getj(c,p):
    r=c.get(p)
    if inspect.isawaitable(r): r=await r
    return r
async def main():
    kid,pem=resolve_kalshi_keys("KALSHI",sec)
    b=KalshiBroker(api_key_id=kid, private_key_pem=pem, demo=False); await b.connect()
    try:
        raw=await getj(b._client,"/markets/%s"%T)
        m=(raw or {}).get("market") or raw or {}
        print("title        =",m.get("title"))
        print("yes_sub_title =",m.get("yes_sub_title"),"   <-- the YES side (what leg=yes resolves to)")
        print("no_sub_title  =",m.get("no_sub_title"))
        print("status/result =",m.get("status"),"/",m.get("result"))
        print("event_ticker  =",m.get("event_ticker"))
    finally:
        await b.disconnect()
asyncio.run(main())
PY
echo "=== DONE cs2_audit ==="
