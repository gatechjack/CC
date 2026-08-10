set -u
cd /home/azureuser/trading_corp || exit 2
# Watch audit batch 1: Kosherlocks,rollobravado,hurrican,imnice,sabsabinxz,GreatestTrader,Cuco84
WALLETS="0x82398835fe16616214d928ba87127e28fc1cd9a3,0x3b62c64ebaee15478e8b21765b9f940458655cc8,0x8718865882e085b764825b637788043c16185040,0x99518fa0d201e7542ea2b852809da5a5c8d811bd,0xd3ecb2aee0d65622da559ff356b00e8c2e626603,0x258c8a3ab3f9dd5c1e3bb05f54a9187247b77c23,0xf3b04dd038fcf191692927c0aa5a38018ea323a1"
cat > /tmp/pct_audit.py <<'PYEOF'
import asyncio, json, sys, time
from trading_corp.data.polymarket_data_api_client import PolymarketDataAPIClient
from trading_corp.data.polymarket_whale_audit import build_audit_report, group_fills_by_decision

NOW = int(time.time())
WEEK = NOW - 7*86400
MAXP = 10
PAGE = 500

async def fetch_all(client, wallet):
    out=[]
    for i in range(MAXP):
        try:
            p = await client.fetch_activity(wallet, limit=PAGE, offset=i*PAGE)
        except Exception as e:
            return out, True, str(e)[:80]
        if not p: break
        out.extend(p)
        if len(p) < PAGE: break
    hit_cap = (len(out) >= MAXP*PAGE)
    return out, hit_cap, ""

def dec_ts_max(d):
    ts=[r.timestamp for r in d.buy_rows]+[r.timestamp for r in d.sell_rows]
    return max(ts) if ts else 0

def dec_ts_min_buy(d):
    ts=[r.timestamp for r in d.buy_rows]
    return min(ts) if ts else 0

async def one(client, wallet):
    act, hit_cap, ferr = await fetch_all(client, wallet)
    if not act:
        return {"wallet":wallet,"error":"no_activity","ferr":ferr}
    cids={a.condition_id for a in act if a.condition_id}
    res=await client.fetch_market_resolutions(list(cids))
    rep=build_audit_report(leaderboard_entry=None, activity_rows=act, resolutions=res, proxy_wallet=wallet)
    decs=group_fills_by_decision(act, res)
    p=rep.realized_pnl; e=rep.edge; c=rep.clustering; cat=rep.category
    nres=rep.n_resolved_decisions; nwin=rep.n_winning_decisions
    wr=round(nwin/nres,3) if nres else 0.0
    buy=rep.total_buy_usdc_resolved
    roi=round(100.0*p.realized_pnl_usdc/buy,2) if buy else 0.0
    wk=[d for d in decs.values() if d.is_resolved and dec_ts_max(d)>=WEEK]
    wk_real=round(sum(d.realized_pnl for d in wk),2)
    wk_clean=round(sum(d.realized_pnl for d in wk if not d.is_partial_sell(0.20)),2)
    wk_n=len(wk); wk_win=sum(1 for d in wk if d.is_winning_side)
    entered_wk=sum(1 for d in decs.values() if dec_ts_min_buy(d)>=WEEK)
    days_since=round((NOW-rep.activity_max_ts)/86400.0,2) if rep.activity_max_ts else None
    return {
      "wallet":wallet,"name":rep.user_name,"n_raw":rep.n_raw_rows_examined,"hit_cap":hit_cap,
      "n_res":nres,"n_win":nwin,"wr":wr,"realized":p.realized_pnl_usdc,"held":p.held_to_resolution_pnl_usdc,
      "infl_ratio":p.pnl_inflation_ratio,"clean":p.pnl_from_clean_holds_usdc,"partial":p.pnl_from_partial_sells_usdc,
      "buy_usdc":round(buy,2),"roi_pct":roi,"avg_px":e.avg_entry_price_decision_weighted,
      "fav85":e.share_above_85,"sub70":e.share_below_70,"clust":c.clustering_ratio,
      "top_event_share":cat.largest_event_share,"n_events":cat.n_distinct_event_slugs,
      "days_since":days_since,"entered_wk":entered_wk,
      "wk_n":wk_n,"wk_win":wk_win,"wk_real":wk_real,"wk_clean":wk_clean
    }

async def main():
    wallets=sys.argv[1].split(",")
    async with PolymarketDataAPIClient() as client:
        for w in wallets:
            try:
                r=await one(client, w)
            except Exception as e:
                r={"wallet":w,"error":"exc","msg":str(e)[:120]}
            print(json.dumps(r))
            sys.stdout.flush()
            await asyncio.sleep(0.7)

asyncio.run(main())
PYEOF
echo "=== NOW_UTC $(date -u +%FT%TZ) ==="
PYTHONPATH=/home/azureuser/trading_corp venv/bin/python /tmp/pct_audit.py "$WALLETS"
echo "=== EXIT $? ==="
rm -f /tmp/pct_audit.py
echo "=== DONE ==="
