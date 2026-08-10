set -u
cd /home/azureuser/trading_corp || exit 2
# Group A = current copy list (selected_whales), 7 wallets. Read-only: API only, no DB/state writes.
WALLETS="0x7714c16f86bcfdba47bfcb161dc39a2a1ff2b814,0x97ead83eb0e6b7f142e63284791882f05ddf3363,0x1f9f03e7ce52979b658b0bb75b483ff923fda025,0xf192501abae4c453cc15ddbe9543ed11e99a6ee2,0x27d2812fa0d04ca1b874cffedff7a15beb4ab0f8,0x4a1b8e8d38aecdc9687bb0f601801d59fa44724f,0xb56db5215443706244b0af76b3daaad3066ad621"
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
    # last-7d ACTIVE resolved decisions (max row ts in window)
    wk=[d for d in decs.values() if d.is_resolved and dec_ts_max(d)>=WEEK]
    wk_real=round(sum(d.realized_pnl for d in wk),2)
    wk_held=round(sum(d.held_to_resolution_pnl for d in wk),2)
    wk_clean=round(sum(d.realized_pnl for d in wk if not d.is_partial_sell(0.20)),2)
    wk_part=round(sum(d.realized_pnl for d in wk if d.is_partial_sell(0.20)),2)
    wk_n=len(wk); wk_win=sum(1 for d in wk if d.is_winning_side)
    # decisions ENTERED this week (min buy ts in window), resolved or not
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
      "wk_n":wk_n,"wk_win":wk_win,"wk_real":wk_real,"wk_held":wk_held,"wk_clean":wk_clean,"wk_part":wk_part
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
echo "=== LAYOUT ===" && ls -d trading_corp data venv 2>&1 | head
echo "=== NOW_UTC $(date -u +%FT%TZ) ==="
PYTHONPATH=/home/azureuser/trading_corp venv/bin/python /tmp/pct_audit.py "$WALLETS"
echo "=== EXIT $? ==="
rm -f /tmp/pct_audit.py
echo "=== DONE ==="
