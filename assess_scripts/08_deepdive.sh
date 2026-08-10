set -u
cd /home/azureuser/trading_corp || exit 2
DB=/home/azureuser/trading_corp/data/trading_corp.db
RO="sqlite3 -readonly $DB"
LWAL="0x7714c16f86bcfdba47bfcb161dc39a2a1ff2b814"
echo "=== NOW ===" && $RO "SELECT datetime('now');"
echo "=== L-COPIED daily (last 60d): date|fills|resolved|net_realized ==="
$RO "SELECT substr(entry_ts,1,10)||'|'||COUNT(*)||'|'||SUM(CASE WHEN resolved_ts IS NOT NULL THEN 1 ELSE 0 END)||'|'||ROUND(SUM(realized_pnl),2) FROM polymarket_round_trips WHERE division='polymarket_copy_trading' AND json_extract(extra_json,'\$.whale_wallet')='$LWAL' AND entry_ts>=datetime('now','-60 days') GROUP BY substr(entry_ts,1,10) ORDER BY substr(entry_ts,1,10);"
echo "=== L-COPIED last entry + first entry + total ==="
$RO "SELECT 'first='||substr(MIN(entry_ts),1,10)||' last='||substr(MAX(entry_ts),1,10)||' n='||COUNT(*) FROM polymarket_round_trips WHERE division='polymarket_copy_trading' AND json_extract(extra_json,'\$.whale_wallet')='$LWAL';"
cat > /tmp/pct_dd.py <<'PYEOF'
import asyncio, json, sys, time
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from trading_corp.data.polymarket_data_api_client import PolymarketDataAPIClient
from trading_corp.data.polymarket_whale_audit import group_fills_by_decision

NOW=int(time.time()); MAXP=25; PAGE=500

async def fetch_all(client, wallet):
    out=[]
    for i in range(MAXP):
        try: p=await client.fetch_activity(wallet, limit=PAGE, offset=i*PAGE)
        except Exception: break
        if not p: break
        out.extend(p)
        if len(p)<PAGE: break
    return out, (len(out)>=MAXP*PAGE)

def d(ts): return datetime.fromtimestamp(ts, timezone.utc).date()
def wk(dt): return (dt - timedelta(days=dt.weekday())).isoformat()

async def one(client, wallet):
    act,cap=await fetch_all(client, wallet)
    if not act: return {"wallet":wallet,"error":"no_activity"}
    trades=[r for r in act if r.type=="TRADE" and r.timestamp>0]
    # daily trade counts
    day_n=defaultdict(int)
    for r in trades: day_n[d(r.timestamp)]+=1
    days=sorted(day_n)
    span_min=days[0].isoformat(); span_max=days[-1].isoformat()
    span_days=(days[-1]-days[0]).days
    # gaps (>=3 calendar days between consecutive active days)
    gaps=[]
    for i in range(1,len(days)):
        g=(days[i]-days[i-1]).days
        if g>=3: gaps.append([days[i-1].isoformat(), days[i].isoformat(), g])
    open_gap=round((NOW-max(r.timestamp for r in trades))/86400.0,2)
    # weekly realized/clean over resolved decisions (bucket by decision max-activity date)
    res=await client.fetch_market_resolutions(list({r.condition_id for r in act if r.condition_id}))
    decs=group_fills_by_decision(act, res)
    wkagg=defaultdict(lambda:[0,0.0,0.0])  # week -> [n_dec, realized, clean]
    for dc in decs.values():
        if not dc.is_resolved: continue
        ts=[r.timestamp for r in dc.buy_rows]+[r.timestamp for r in dc.sell_rows]
        if not ts: continue
        w=wk(d(max(ts)))
        wkagg[w][0]+=1; wkagg[w][1]+=dc.realized_pnl
        if not dc.is_partial_sell(0.20): wkagg[w][2]+=dc.realized_pnl
    weeks=sorted(wkagg)
    cum=0.0; weekly=[]
    for w in weeks:
        n,real,clean=wkagg[w]; cum+=real
        weekly.append([w,n,round(real,1),round(clean,1),round(cum,1)])
    return {"wallet":wallet,"name":act[0].name,"n_raw":len(act),"hit_cap":cap,
            "span_min":span_min,"span_max":span_max,"span_days":span_days,
            "open_gap_days":open_gap,"gaps_ge3d":gaps,"weekly_realized_clean_cum":weekly}

async def main():
    async with PolymarketDataAPIClient() as client:
        for w in sys.argv[1].split(","):
            try: r=await one(client, w)
            except Exception as e: r={"wallet":w,"error":str(e)[:120]}
            print(json.dumps(r)); sys.stdout.flush()
            await asyncio.sleep(0.5)

asyncio.run(main())
PYEOF
echo "=== NOW_UTC $(date -u +%FT%TZ) ==="
PYTHONPATH=/home/azureuser/trading_corp venv/bin/python /tmp/pct_dd.py "0x7714c16f86bcfdba47bfcb161dc39a2a1ff2b814,0x258c8a3ab3f9dd5c1e3bb05f54a9187247b77c23"
echo "=== EXIT $? ==="
rm -f /tmp/pct_dd.py
echo "=== DONE ==="
