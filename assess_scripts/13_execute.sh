set -u
cd /home/azureuser/trading_corp || exit 2
H="http://127.0.0.1:8000"
PROMOTE="0x3b62c64ebaee15478e8b21765b9f940458655cc8 0x82398835fe16616214d928ba87127e28fc1cd9a3 0x258c8a3ab3f9dd5c1e3bb05f54a9187247b77c23 0x48898de94e70ca0c06c62c57483b3a8d5890e2d8"
DEMOTE="0xf192501abae4c453cc15ddbe9543ed11e99a6ee2 0x1f9f03e7ce52979b658b0bb75b483ff923fda025 0x7714c16f86bcfdba47bfcb161dc39a2a1ff2b814"
echo "=== PREFLIGHT (GET on POST route must be 405) ==="
pc=$(curl -s -o /dev/null -m 10 -w "%{http_code}" "$H/api/polymarket/watchlist/promote/0x0000000000000000000000000000000000000000")
dc=$(curl -s -o /dev/null -m 10 -w "%{http_code}" "$H/api/polymarket/whales/demote/0x0000000000000000000000000000000000000000")
echo "promote_route=$pc demote_route=$dc"
if [ "$pc" != "405" ] || [ "$dc" != "405" ]; then echo "ABORT: routes not confirmed (need 405/405)"; exit 3; fi
echo "=== PROMOTES (add to selected + pinned) ==="
for w in $PROMOTE; do
  b=$(curl -s -m 25 -w "|HTTP%{http_code}" -X POST "$H/api/polymarket/watchlist/promote/$w")
  echo "promote $w -> $b"
done
echo "=== DEMOTES (remove from selected+pinned; flatten paper book) ==="
for w in $DEMOTE; do
  b=$(curl -s -m 25 -w "|HTTP%{http_code}" -X POST "$H/api/polymarket/whales/demote/$w")
  echo "demote $w -> $b"
done
cat > /tmp/pct_bespoke.py <<'PYEOF'
from datetime import datetime, timezone
from trading_corp.persistence.db import load_agent_state, set_agent_state
NOW=datetime.now(timezone.utc).isoformat()
AG="polymarket_copy_trader"
GT="0x258c8a3ab3f9dd5c1e3bb05f54a9187247b77c23"
LLLL="0x7714c16f86bcfdba47bfcb161dc39a2a1ff2b814"
GTNOTE=("running hot: last 2wk=58% of PnL; size conservatively; +$142k not a run-rate; "
        "durability confirmed 51d/7-of-8-wk (assessment 2026-08-09)")
def wal(x): return str(x.get("wallet") or x.get("proxy_wallet") or "").lower()
for key in ("selected_whales","pinned_whales"):
    rec=load_agent_state(AG,key); lst=list(rec[0]) if rec else []
    hit=0
    for e in lst:
        if isinstance(e,dict) and wal(e)==GT:
            e["note"]=GTNOTE; hit+=1
    set_agent_state(AG,key,lst)
    print(f"[GTnote] {key}: set_on={hit} len={len(lst)} members={[wal(e)[:8] for e in lst if isinstance(e,dict)]}")
rec=load_agent_state(AG,"watch_only_whales"); watch=list(rec[0]) if rec else []
present=any(isinstance(e,dict) and wal(e)==LLLL for e in watch)
if not present:
    watch.append({"rank":None,"proxy_wallet":LLLL,
      "user_name":"llllllIIIIIIlIllllllIIIIIIlIllllllIIIIIIlI","x_username":"","verified_badge":False,
      "total_resolved_positions":694,"wins":406,"losses":288,"win_rate":0.585,
      "realized_pnl_usdc":5037.6,"total_usdc_size_resolved":2600017.38,
      "lifetime_pnl_from_leaderboard":0.0,"lifetime_vol_from_leaderboard":0.0,"best_category":"Sports",
      "included_iso":NOW,"window_size_n":694,"window_days_span":94.0,
      "last_trade_iso":"2026-07-31T00:00:00+00:00","provisional":False,
      "avg_entry_price":0.5667,"share_below_70":0.7594,
      "note":("DEMOTED 2026-08-10: copyable signal stopped ~9d, no LoL-calendar excuse; "
              "own-edge mildly positive (uncapped realized +$5,038 / clean +$7,328). "
              "Preserve option; HARD-CUT if not resumed by 2026-08-17."),
      "source":"assessment_demote_2026-08-09"})
set_agent_state(AG,"watch_only_whales",watch)
print(f"[watch] llllllII_present_before={present} len_after={len(watch)}")
PYEOF
echo "=== BESPOKE (GT note + llllllII watch entry) ==="
PYTHONPATH=/home/azureuser/trading_corp venv/bin/python /tmp/pct_bespoke.py
echo "=== EXIT $? ==="
rm -f /tmp/pct_bespoke.py
echo "=== DONE ==="
