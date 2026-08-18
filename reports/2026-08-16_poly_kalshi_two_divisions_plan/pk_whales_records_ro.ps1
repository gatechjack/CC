# pk_whales_records_ro.ps1 -- READ-ONLY, COMPACT, SELF-CONTAINED: both whale-record sets in one small
# output (summary-per-whale only, no per-copy detail) so all 4 whales fit under the az ~4KB message
# limit (which truncated SDTrading in pk_whales_set2_ro). SET 1 is self-contained -- it uses ONLY
# PolymarketDataAPIClient (which the live loop imports, so it's guaranteed on the box) + an inline
# held-to-resolution win/PnL, avoiding trading_corp.data.whale_screening (NOT on the box: pk_whales_set1
# failed on that import) and build_audit_report. GROSS (no fees) in both sets. No writes. Run:
#   powershell -ep bypass -f .\pk_whales_records_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
venv/bin/python3 - <<'PY'
import os, json, asyncio
try:
    from dotenv import load_dotenv; load_dotenv("/home/azureuser/trading_corp/.env")
except Exception:
    pass
DB = os.environ.get("TRADING_CORP_DB_URL", "sqlite:///data/trading_corp.db")
from trading_corp.persistence import db
rec = db.load_agent_state("poly_kalshi_mlb", "live_whales", db_url=DB)
live = rec[0] if rec else []
WH = [((w.get("user_name") or ""), (w.get("wallet") or "").lower()) for w in live if isinstance(w, dict)]
print("LIVE_WHALES n=%d :" % len(WH), [n for n, w in WH])

# === SET 2: our Kalshi copies (DB; MLB-only by construction; GROSS) -- SUMMARY per whale ===
print("=== SET 2 (our Kalshi copies; GROSS) ===")
tot = dict(placed=0, settled=0, open=0, wins=0, losses=0, realized=0.0)
with db.connect(DB) as c:
    for name, w in WH:
        rows = c.execute("select payload_json from audit_event where actor='poly_kalshi_mlb' and kind='poly_kalshi_order' and lower(json_extract(payload_json,'$.whale_wallet'))=?", (w,)).fetchall()
        placed = [json.loads(r[0]) for r in rows]
        placed = [p for p in placed if p.get("status") == "placed" and (p.get("action") or "entry") == "entry" and p.get("order_id")]
        oids = [p["order_id"] for p in placed]
        settled = {}
        if oids:
            q = "select order_id,won,realized_pnl from kalshi_round_trips where division='poly_kalshi_mlb' and order_id in (%s)" % ",".join("?" * len(oids))
            for rr in c.execute(q, oids).fetchall():
                settled[rr[0]] = (rr[1], rr[2])
        n_p = len(placed); n_s = len(settled); n_o = n_p - n_s
        wins = sum(1 for wl, _ in settled.values() if wl); losses = n_s - wins
        realized = round(sum(pn for _, pn in settled.values()), 2)
        wr = (100.0 * wins / n_s) if n_s else 0.0
        print("SET2 %-26s placed=%d settled=%d open=%d wins=%d losses=%d winrate=%.0f%% realized_gross=$%.2f" % (name or w[:10], n_p, n_s, n_o, wins, losses, wr, realized))
        tot["placed"] += n_p; tot["settled"] += n_s; tot["open"] += n_o; tot["wins"] += wins; tot["losses"] += losses; tot["realized"] += realized
print("SET2 TOTAL placed=%d settled=%d open=%d wins=%d losses=%d realized_gross=$%.2f" % (tot["placed"], tot["settled"], tot["open"], tot["wins"], tot["losses"], round(tot["realized"], 2)))

# === SET 1: whales' MLB record on Polymarket -- self-contained held-to-resolution recompute (GROSS) ===
print("=== SET 1 (whales' MLB record on Polymarket; windowed; held-to-resolution; GROSS) ===")
from trading_corp.data.polymarket_data_api_client import PolymarketDataAPIClient
def is_mlb(a):
    s = (str(getattr(a, "event_slug", "") or "") or str(getattr(a, "slug", "") or "")).lower()
    return s.startswith("mlb-")
async def fetch_all(client, w, limit=300, max_pages=8):
    out = []
    for pg in range(max_pages):
        try:
            page = await client.fetch_activity(w, limit=limit, offset=pg * limit)
        except Exception as e:
            return out, "err:%s" % e
        out.extend(page)
        if len(page) < limit:
            return out, "exhausted@p%d" % (pg + 1)
    return out, "max_pages"
async def main():
    async with PolymarketDataAPIClient() as client:
        for name, w in WH:
            rows, reason = await fetch_all(client, w)
            mlb_buys = [a for a in rows if getattr(a, "type", "") == "TRADE" and getattr(a, "side", "") == "BUY" and is_mlb(a)]
            cids = sorted({a.condition_id for a in mlb_buys if a.condition_id})
            try:
                res = await client.fetch_market_resolutions(cids) if cids else {}
            except Exception as e:
                res = {}; print("   %s resolutions_err %s" % (w[:10], e))
            n_res = wins = 0; pnl = 0.0; buy_usdc = 0.0
            for a in mlb_buys:
                r = res.get(a.condition_id)
                if not r or r.get("status") != "resolved" or r.get("winning_outcome_index") is None:
                    continue
                n_res += 1
                won = (int(a.outcome_index) == int(r["winning_outcome_index"]))
                if won: wins += 1
                pnl += (a.size * (1.0 - a.price)) if won else (-a.size * a.price)
                buy_usdc += a.usdc_size
            wr = (100.0 * wins / n_res) if n_res else 0.0
            print("SET1 %-26s window=%d(%s) MLB_buys=%d resolved=%d wins=%d winrate=%.0f%% realized_gross=$%.2f buy_usdc=$%.0f" % (
                name or w[:10], len(rows), reason, len(mlb_buys), n_res, wins, wr, round(pnl, 2), round(buy_usdc, 0)))
asyncio.run(main())
PY
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$sh = "printf %s '$b64' | base64 -d | bash"
Write-Host "== whale MLB records (READ-ONLY, compact): SET2 Kalshi copies + SET1 Polymarket recompute =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts $sh --query "value[0].message" -o tsv
