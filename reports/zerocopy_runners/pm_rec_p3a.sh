PY=/home/azureuser/trading_corp/venv/bin/python
ROOT=/home/azureuser/trading_corp
cd "$ROOT" || exit 2
export KEY_VAULT_URI=$(systemctl show trading-corp -p Environment | grep -oE 'KEY_VAULT_URI=[^ ]+' | head -1 | cut -d= -f2-)
echo "KEY_VAULT_URI present: ${KEY_VAULT_URI:+yes}"
echo "===== 3.1 READ-ONLY RECONCILE PROOF (boot_reconcile.compare per account; NO latch, NO write) ====="
PYTHONPATH=. "$PY" - <<'PY'
import asyncio, sqlite3
from trading_corp.utils import secrets as S
from trading_corp.prediction_markets.shard_snapshot_task import resolve_kalshi_keys
from trading_corp.brokers.kalshi import KalshiBroker
from trading_corp.prediction_markets import boot_reconcile as BR
UFC="KXUFCFIGHT-26SEP29BULVIS-VIS"
ACCTS=[("kalshi_jack","KALSHI"),("kalshi_karen","kalshi_karen"),("kalshi_marc","kalshi_marc"),("kalshi_trey","kalshi_trey")]
sec=S.load_secrets()
pm=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True)
async def get_positions(ref):
    kid,pem=resolve_kalshi_keys(ref,sec)
    if not (kid and pem): raise RuntimeError("no keys for ref %s"%ref)
    b=KalshiBroker(api_key_id=kid, private_key_pem=pem, demo=False)
    await b.connect()
    try:
        last=None
        for attempt in range(3):
            try:
                return list(await b._client.portfolio.get_positions(fetch_all=True))
            except Exception as e:
                last=e; await asyncio.sleep(1.0)
        raise last
    finally:
        await b.disconnect()
async def main():
    allclean=True
    for aid,ref in ACCTS:
        j=BR.journal_signed_positions(pm, aid)
        try:
            poss=await get_positions(ref)
        except Exception as e:
            print("%-14s VENUE READ FAILED: %r -> INCONCLUSIVE (cannot confirm; do NOT clear)"%(aid,e)); allclean=False; continue
        k=BR.kalshi_signed_positions(poss)
        diffs=BR.compare(j,k)
        print("%-14s journal_tickers=%s kalshi_tickers=%s DIFFS=%s"%(aid,len(j),len(k),len(diffs)))
        for d in diffs:
            print("    DIFF %s j=%s k=%s [%s]"%(d.ticker,d.journal_signed,d.kalshi_signed,d.classification))
        print("    UFC ticker: journal_signed=%s kalshi_signed=%s %s"%(j.get(UFC,0),k.get(UFC,0),"AGREE" if j.get(UFC,0)==k.get(UFC,0) else "DISAGREE"))
        if diffs: allclean=False
    print("RECONCILE_ALL_CLEAN:", allclean)
asyncio.run(main())
PY
echo "===== 3.2-PREP: can the pre-latch ARMED set be reconstructed? (legacy RO) ====="
"$PY" - <<'PY'
import sqlite3, json
lg=sqlite3.connect('file:/home/azureuser/trading_corp/data/trading_corp.db?mode=ro',uri=True)
print("legacy tables with arm/audit/hist in name:")
for r in lg.execute("select name from sqlite_master where type='table' and (name like '%arm%' or name like '%audit%' or name like '%hist%' or name like '%agent%') order by name"):
    print("   ",r[0])
# audit_event: any pm_live / arm rows?
try:
    cols=[c[1] for c in lg.execute("pragma table_info(audit_event)")]
    print("audit_event cols:",cols)
    print("audit_event rows actor='pm_live':", lg.execute("select count(*) from audit_event where actor='pm_live'").fetchone()[0])
    print("audit_event kinds mentioning arm:")
    for r in lg.execute("select kind,count(*) from audit_event where lower(kind) like '%arm%' or lower(kind) like '%disarm%' group by kind"):
        print("   ",r[0],r[1])
except Exception as e: print("audit_event probe ERR:",e)
# agent_state: current arm rows -- all latched? any still armed? updated_ts spread
print("-- agent_state pm_live arm rows: armed/latched/updated_ts (full) --")
rows=lg.execute("select key,value_json,updated_ts from agent_state where agent='pm_live' and key like 'arm:%' order by key").fetchall()
narm=0;nlatch=0;ndisarm_unlatched=0
for k,v,u in rows:
    d=json.loads(v) if v else {}
    a=d.get('armed'); l=d.get('latched')
    if a is True: narm+=1
    elif l: nlatch+=1
    else: ndisarm_unlatched+=1
print("   total",len(rows),"armed=True",narm,"armed!=True&latched",nlatch,"disarmed&NOT latched",ndisarm_unlatched)
# sample one full latched value_json to confirm NO prior-armed field
for k,v,u in rows:
    d=json.loads(v) if v else {}
    if d.get('latched'):
        print("   sample latched full keys:", sorted(d.keys()), "->", d); break
PY
echo "===== 3.2-PREP: journal arm-set enumeration search (bounded 2026-10-05 12:00 .. 10-06 02:58) ====="
journalctl -u trading-corp --since "2026-10-05 12:00:00" --until "2026-10-06 02:58:30" --no-pager -o short-iso 2>&1 | grep -iE "armed subs|arm:kalshi|live-arm|subs armed|arm_gated|[0-9]+ armed|disarm" | head -30 || echo "(no arm-enumeration lines)"
echo "===== DONE p3a ====="
