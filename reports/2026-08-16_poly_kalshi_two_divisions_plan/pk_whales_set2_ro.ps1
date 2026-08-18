# pk_whales_set2_ro.ps1 -- READ-ONLY: SET 2 = OUR realized MLB copies on Kalshi (the posterior).
# Confirms live_whales, then per whale reports our poly_kalshi_mlb copies: placed / settled / open,
# realized NET-of-nothing (GROSS) P&L, win/loss on settled, per-copy detail. MLB-only by construction
# (the division only copies MLB single-game moneyline). No writes. Run:
#   powershell -ep bypass -f .\pk_whales_set2_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
venv/bin/python3 - <<'PY'
import os, json
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
for n, w in WH:
    print("  WHALE", w, n)
print("=== SET 2: OUR realized MLB copies on Kalshi (division=poly_kalshi_mlb) -- MLB-only by construction; realized P&L is GROSS (no fee term; fill_fee journaled but not netted) ===")
with db.connect(DB) as c:
    for name, w in WH:
        rows = c.execute("select ts,payload_json from audit_event where actor='poly_kalshi_mlb' and kind='poly_kalshi_order' and lower(json_extract(payload_json,'$.whale_wallet'))=? order by ts", (w,)).fetchall()
        allp = [(r[0], json.loads(r[1])) for r in rows]
        placed = [(ts, p) for ts, p in allp if p.get("status") == "placed" and (p.get("action") or "entry") == "entry" and p.get("order_id")]
        other = {}
        for ts, p in allp:
            if not (p.get("status") == "placed" and (p.get("action") or "entry") == "entry" and p.get("order_id")):
                other[p.get("status")] = other.get(p.get("status"), 0) + 1
        oids = [p["order_id"] for ts, p in placed]
        settled = {}
        if oids:
            q = "select order_id,won,realized_pnl,market_result,resolved_ts from kalshi_round_trips where division='poly_kalshi_mlb' and order_id in (%s)" % ",".join("?" * len(oids))
            for rr in c.execute(q, oids).fetchall():
                settled[rr[0]] = dict(won=rr[1], realized=rr[2], result=rr[3], resolved_ts=rr[4])
        n_p = len(placed); n_s = len(settled); n_o = n_p - n_s
        wins = sum(1 for s in settled.values() if s["won"]); losses = n_s - wins
        realized = round(sum(s["realized"] for s in settled.values()), 2)
        print("W2 %-26s | placed=%d settled=%d open=%d | wins=%d losses=%d | realized_gross=$%.2f | non-placed_status=%s" % (
            (name or w[:10]), n_p, n_s, n_o, wins, losses, realized, (dict(other) or "-")))
        if n_p == 0:
            print("   (no live MLB copies yet for this whale -- SET 2 empty)")
        for ts, p in placed:
            s = settled.get(p["order_id"])
            tag = ("SETTLED won=%s realized_gross=$%.2f result=%s resolved_ts=%s" % (s["won"], s["realized"], s["result"], s["resolved_ts"])) if s else "OPEN (riding to settlement)"
            print("   our_ts=%s | ticker=%s side=%s count=%s fill_price=%s stake=$%s | %s" % (
                ts, p.get("ticker"), p.get("side"), (p.get("fill_count") or p.get("count")), p.get("fill_price"), p.get("stake_usd"), tag))
print("LAG_NOTE: poly->kalshi lag is NOT recoverable from persisted data -- the Poly trigger timestamp (seen_ts/action_ts/latency_s) lives ONLY in the in-memory shadow_log; the persisted trigger dict carries poly_slug/outcome/side but NO poly timestamp. Only OUR placement ts (our_ts above) is stored.")
PY
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$sh = "printf %s '$b64' | base64 -d | bash"
Write-Host "== SET 2 (READ-ONLY): our realized MLB copies on Kalshi =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts $sh --query "value[0].message" -o tsv
