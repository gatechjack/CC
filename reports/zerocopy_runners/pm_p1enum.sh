PY=/home/azureuser/trading_corp/venv/bin/python
ROOT=/home/azureuser/trading_corp
cd "$ROOT" || exit 2
export KEY_VAULT_URI=$(systemctl show trading-corp -p Environment | grep -oE 'KEY_VAULT_URI=[^ ]+' | head -1 | cut -d= -f2-)
echo "KEY_VAULT_URI present: ${KEY_VAULT_URI:+yes}"
echo "=== Kalshi /portfolio/settlements market_result ENUMERATION across history (RO) ==="
PYTHONPATH=. "$PY" - <<'PY'
import asyncio, inspect, json
from trading_corp.utils import secrets as S
from trading_corp.prediction_markets.shard_snapshot_task import resolve_kalshi_keys
from trading_corp.brokers.kalshi import KalshiBroker
ACCTS=[("kalshi_jack","KALSHI"),("kalshi_karen","kalshi_karen"),("kalshi_marc","kalshi_marc"),("kalshi_trey","kalshi_trey")]
sec=S.load_secrets()
async def getj(c,p):
    r=c.get(p)
    if inspect.isawaitable(r): r=await r
    return r
dist={}; sample={}; unknown=[]
async def scan(aid,ref):
    kid,pem=resolve_kalshi_keys(ref,sec)
    b=KalshiBroker(api_key_id=kid, private_key_pem=pem, demo=False); await b.connect()
    try:
        cur=""; pages=0; n=0
        while pages<25:
            raw=await getj(b._client,"/portfolio/settlements?limit=200"+("&cursor=%s"%cur if cur else ""))
            items=(raw or {}).get("settlements") or []
            for it in items:
                n+=1
                mr=str(it.get("market_result") or it.get("result") or "").strip().lower()
                dist[mr]=dist.get(mr,0)+1
                if mr not in sample:
                    sample[mr]=it   # first full record of this class
                if mr not in ("yes","no","void","scalar"):
                    unknown.append((aid,it.get("ticker"),mr))
            cur=(raw or {}).get("cursor") or ""; pages+=1
            if not cur or not items: break
        print("  %-14s scanned=%s pages=%s"%(aid,n,pages))
    finally:
        await b.disconnect()
async def main():
    for aid,ref in ACCTS: await scan(aid,ref)
    print("\n=== market_result distribution (all accounts, history) ===")
    for k,v in sorted(dist.items(), key=lambda x:-x[1]): print("   %-10s %s"%(k or "(empty)",v))
    print("\n=== one full record per market_result class (keys+values) ===")
    for k in sorted(sample):
        it=sample[k]
        print("--- class=%r ---"%k)
        for kk in sorted(it.keys()):
            print("     %-26s %r"%(kk, it[kk]))
    print("\n=== UNKNOWN-class records (not in {yes,no,void,scalar}) ===")
    if unknown:
        for a,t,mr in unknown[:30]: print("   ",a,t,mr)
    else:
        print("   (none in scanned history)")
asyncio.run(main())
PY
echo "=== DONE p1enum ==="
