# pk_stage1_rebaseline_ro.ps1 -- READ-ONLY Stage-1 re-baseline after MACE's deploy.
# Reports: engine PID, poly_kalshi_mlb armed status + halt + roster + mark tables, and the
# box-current LF-md5 of the 3 deploy files. NO writes, NO restart, NO deploy.
# Run:
#   powershell -ep bypass -f .\pk_stage1_rebaseline_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
echo "=== 1. ENGINE PID ==="
echo "MainPID = $(systemctl show -p MainPID --value trading-corp 2>/dev/null)"
echo ""
echo "=== 2. poly_kalshi_mlb ARMED + halt + roster + mark tables ==="
venv/bin/python3 - <<'PY'
import os, yaml
try:
    from dotenv import load_dotenv; load_dotenv("/home/azureuser/trading_corp/.env")
except Exception:
    pass
cfg = (yaml.safe_load(open("config/strategies.yaml")) or {}).get("poly_kalshi_mlb") or {}
auto = bool(cfg.get("auto_execute", False))
print("enabled      =", cfg.get("enabled"))
print("auto_execute =", auto, " -> dry_run =", (not auto))
print("stake_usd    =", cfg.get("stake_usd"), " daily_loss_cap =", cfg.get("daily_loss_cap_usd"),
      " max_orders_per_day =", cfg.get("max_orders_per_day"))
ractor = cfg.get("roster_actor", "polymarket_copy_trader")
rkey = cfg.get("roster_key", "selected_whales")
print("roster_actor =", ractor, " roster_key =", rkey)
DB = os.environ.get("TRADING_CORP_DB_URL", "sqlite:///data/trading_corp.db")
from trading_corp.persistence import db
from trading_corp.persistence.models import StrategyState
from trading_corp.persistence.db import load_agent_state
print("halted       =", StrategyState.from_persistence("poly_kalshi_mlb", db_url=DB).halted)
def _count(actor, key):
    rec = load_agent_state(actor, key, db_url=DB)
    if not rec:
        return "MISSING/empty"
    v = rec[0]
    return len(v) if isinstance(v, list) else ("scalar:%r" % (v,))
print("live_whales count               =", _count("poly_kalshi_mlb", "live_whales"))
print("configured roster count         =", _count(ractor, rkey))
print("selected_whales (paper) count   =", _count("polymarket_copy_trader", "selected_whales"))
with db.connect(DB) as c:
    for t in ("poly_kalshi_mark_live", "poly_kalshi_mark_history"):
        try:
            n = c.execute("select count(*) from %s" % t).fetchone()[0]
            print("table %-26s rows = %s" % (t, n))
        except Exception as e:
            print("table %-26s MISSING: %s" % (t, e))
    try:
        n = c.execute("select count(*) from audit_event where actor='poly_kalshi_mlb' "
                      "and kind='poly_kalshi_order' and json_extract(payload_json,'$.status')='placed'").fetchone()[0]
        print("poly_kalshi placed-order rows (lifetime) =", n)
    except Exception as e:
        print("placed-order count failed:", e)
PY
echo ""
echo "=== 3. BOX-CURRENT LF-md5 of the 3 deploy files (tr -d CR | md5sum) ==="
for f in trading_corp/agents/strategies/poly_kalshi_executor.py trading_corp/data/mlb_poly_kalshi_match.py trading_corp/brokers/kalshi.py; do
  printf "%-58s " "$f"; tr -d '\r' < "$f" | md5sum | cut -d' ' -f1
done
echo ""
echo "=== 4. boot log: poly_kalshi wiring + roster invariant (most recent) ==="
journalctl -u trading-corp --no-pager -o cat 2>/dev/null | grep -E "Poly->Kalshi MLB copy WIRED|roster invariant" | tail -4
echo "=== journal most-recent poly_kalshi mark tick (marked>0 would confirm Item2 need) ==="
journalctl -u trading-corp --no-pager -o cat 2>/dev/null | grep "poly_kalshi mark tick" | tail -2
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$sh = "printf %s '$b64' | base64 -d | bash"
Write-Host "== poly_kalshi STAGE-1 RE-BASELINE (READ-ONLY) post-MACE =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts $sh --query "value[0].message" -o tsv
