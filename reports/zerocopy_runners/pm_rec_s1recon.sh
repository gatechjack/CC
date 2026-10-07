PY=/home/azureuser/trading_corp/venv/bin/python
ROOT=/home/azureuser/trading_corp
cd "$ROOT" || exit 2
export KEY_VAULT_URI=$(systemctl show trading-corp -p Environment | grep -oE 'KEY_VAULT_URI=[^ ]+' | head -1 | cut -d= -f2-)
echo "KEY_VAULT_URI present: ${KEY_VAULT_URI:+yes}"
echo "=== STEP 1a: FRESH RECONCILE RE-CHECK (RO; boot_reconcile.compare; NO latch, NO write) ==="
PYTHONPATH=. "$PY" - <<'PY'
import asyncio, sqlite3
from trading_corp.utils import secrets as S
from trading_corp.prediction_markets.shard_snapshot_task import resolve_kalshi_keys
from trading_corp.brokers.kalshi import KalshiBroker
from trading_corp.prediction_markets import boot_reconcile as BR
ACCTS=[("kalshi_jack","KALSHI"),("kalshi_karen","kalshi_karen"),("kalshi_marc","kalshi_marc"),("kalshi_trey","kalshi_trey")]
sec=S.load_secrets()
pm=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True)
async def get_positions(ref):
    kid,pem=resolve_kalshi_keys(ref,sec)
    if not (kid and pem): raise RuntimeError("no keys %s"%ref)
    b=KalshiBroker(api_key_id=kid, private_key_pem=pem, demo=False); await b.connect()
    try:
        last=None
        for _ in range(3):
            try: return list(await b._client.portfolio.get_positions(fetch_all=True))
            except Exception as e: last=e; await asyncio.sleep(1.0)
        raise last
    finally: await b.disconnect()
async def main():
    clean=True
    for aid,ref in ACCTS:
        j=BR.journal_signed_positions(pm,aid)
        try: poss=await get_positions(ref)
        except Exception as e:
            print("%-14s VENUE READ FAILED: %r -> INCONCLUSIVE (STOP, do NOT arm)"%(aid,e)); clean=False; continue
        k=BR.kalshi_signed_positions(poss); diffs=BR.compare(j,k)
        print("%-14s journal_tickers=%s kalshi_tickers=%s DIFFS=%s"%(aid,len(j),len(k),len(diffs)))
        for d in diffs: print("    DIFF %s j=%s k=%s [%s]"%(d.ticker,d.journal_signed,d.kalshi_signed,d.classification))
        if diffs: clean=False
    print("RECONCILE_ALL_CLEAN:", clean)
    print("ARM_GATE:", "OPEN (clean -> arm authorized)" if clean else "CLOSED (STOP -- arm nothing)")
asyncio.run(main())
PY
echo "=== DONE s1recon ==="
