# pk_whales_set1_ro.ps1 -- READ-ONLY: SET 1 = the whales' MLB record on Polymarket (the prior).
# The scorer's STORED stats are all-category BLENDED (not MLB-sliceable) + only cached on-demand, so this
# RECOMPUTES MLB-only from the raw /activity feed (a read-only public Polymarket API fetch, the same one
# the scorer uses -- NO auth, NO writes) filtered to MLB (event_slug/slug startswith 'mlb-'), then runs
# the SAME build_audit_report the production scorer uses. Realized P&L is GROSS (no fee term anywhere in
# the scorer). Windowed (recent history sample, not necessarily lifetime) -- window + n reported honestly.
# NOTE: this makes live network calls from the box (bounded, 4 wallets); it is read-only. Run:
#   powershell -ep bypass -f .\pk_whales_set1_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
venv/bin/python3 - <<'PY'
import os, asyncio
try:
    from dotenv import load_dotenv; load_dotenv("/home/azureuser/trading_corp/.env")
except Exception:
    pass
DB = os.environ.get("TRADING_CORP_DB_URL", "sqlite:///data/trading_corp.db")
from trading_corp.persistence import db
rec = db.load_agent_state("poly_kalshi_mlb", "live_whales", db_url=DB)
live = rec[0] if rec else []
WH = [((w.get("user_name") or ""), (w.get("wallet") or "").lower()) for w in live if isinstance(w, dict)]
print("LIVE_WHALES n=%d" % len(WH))
print("=== SET 1: whales' MLB record on Polymarket -- RECOMPUTED MLB-only from raw /activity (windowed live fetch). GROSS (no fee). The scorer's stored stats are all-category-BLENDED, NOT sliceable to MLB. ===")
from trading_corp.data.polymarket_data_api_client import PolymarketDataAPIClient
from trading_corp.data.whale_screening import _fetch_wallet_activity_windowed
from trading_corp.data.polymarket_whale_audit import build_audit_report
from trading_corp.data.mlb_poly_kalshi_match import parse_poly_mlb_bet
def is_mlb(a):
    s = (str(getattr(a, "event_slug", "") or "") or str(getattr(a, "slug", "") or "")).lower()
    return s.startswith("mlb-")
def is_ml(a):
    try:
        return getattr(parse_poly_mlb_bet(a.slug, a.outcome or "", a.title or "", a.event_slug or ""), "market_type", "") == "moneyline"
    except Exception:
        return False
async def main():
    async with PolymarketDataAPIClient() as client:
        for name, w in WH:
            try:
                rows, pages, reason = await _fetch_wallet_activity_windowed(client, w, activity_limit=300, max_pages=8, target_buy_rows=200)
            except Exception as e:
                print("W1 %-26s FETCH_ERR %s" % ((name or w[:10]), e)); continue
            mlb = [a for a in rows if is_mlb(a)]
            n_ml = sum(1 for a in mlb if is_ml(a))
            cids = sorted({a.condition_id for a in mlb if getattr(a, "side", "") == "BUY" and getattr(a, "condition_id", "")})
            resolutions = {}
            try:
                resolutions = await client.fetch_market_resolutions(cids) if cids else {}
            except Exception as e:
                print("   %s resolutions_err %s" % (w[:10], e))
            try:
                rep = build_audit_report(leaderboard_entry=None, activity_rows=mlb, resolutions=resolutions, proxy_wallet=w)
            except Exception as e:
                print("W1 %-26s AUDIT_ERR %s (window=%d mlb=%d)" % ((name or w[:10]), e, len(rows), len(mlb))); continue
            nres = rep.n_resolved_decisions; nwin = rep.n_winning_decisions
            wr = (100.0 * nwin / nres) if nres else 0.0
            print("W1 %-26s | window=%d rows (pages=%d %s) | MLB rows=%d (moneyline=%d) | resolved=%d wins=%d winrate=%.1f%% | realized_gross=$%.2f held_to_res=$%.2f buy_usdc=$%.2f" % (
                (name or w[:10]), len(rows), pages, reason, len(mlb), n_ml, nres, nwin, wr, rep.realized_pnl.realized_pnl_usdc, rep.realized_pnl.held_to_resolution_pnl_usdc, rep.total_buy_usdc_resolved))
            if nres == 0:
                print("   (no RESOLVED MLB positions in the window -- can't compute MLB win/PnL for this whale from this window)")
asyncio.run(main())
PY
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$sh = "printf %s '$b64' | base64 -d | bash"
Write-Host "== SET 1 (READ-ONLY): whales' MLB record on Polymarket (windowed recompute; makes bounded network calls) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts $sh --query "value[0].message" -o tsv
