# pk_wrap_verify_ro.ps1 -- READ-ONLY session-wrap confirm. NO writes.
# PID + armed + roster, the 3 deployed files + 3 byte-locked LF-md5, and backups retained.
# Run: powershell -ep bypass -f .\pk_wrap_verify_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
echo "MainPID = $(systemctl show -p MainPID --value trading-corp 2>/dev/null)"
echo "=== deployed 3 files (exec 257f6433 / match 7c191e83 / kalshi 7fb2688f) + byte-locked 3 LF-md5 ==="
for f in trading_corp/agents/strategies/poly_kalshi_executor.py trading_corp/data/mlb_poly_kalshi_match.py trading_corp/brokers/kalshi.py trading_corp/agents/strategies/kalshi_copy_trader.py trading_corp/data/sports_team_mapping.py trading_corp/brokers/kalshi_live.py; do
  printf "%-58s " "$f"; tr -d '\r' < "$f" | md5sum | cut -d' ' -f1
done
echo "=== armed + roster ==="
venv/bin/python3 - <<'PY'
import os, yaml
try:
    from dotenv import load_dotenv; load_dotenv("/home/azureuser/trading_corp/.env")
except Exception:
    pass
cfg = (yaml.safe_load(open("config/strategies.yaml")) or {}).get("poly_kalshi_mlb") or {}
auto = bool(cfg.get("auto_execute", False))
from trading_corp.persistence.models import StrategyState
from trading_corp.persistence.db import load_agent_state
DB = os.environ.get("TRADING_CORP_DB_URL", "sqlite:///data/trading_corp.db")
print("auto_execute =", auto, " dry_run =", (not auto),
      " halted =", StrategyState.from_persistence("poly_kalshi_mlb", db_url=DB).halted)
def _n(actor, key):
    rec = load_agent_state(actor, key, db_url=DB)
    return (len(rec[0]) if rec and isinstance(rec[0], list) else "MISSING")
print("live_whales =", _n("poly_kalshi_mlb", "live_whales"),
      " selected_whales(paper) =", _n("polymarket_copy_trader", "selected_whales"))
PY
echo "=== backups retained ==="
for bk in \
  trading_corp/agents/strategies/poly_kalshi_executor.py.bak_item1_20260819_104959 \
  trading_corp/data/mlb_poly_kalshi_match.py.bak_item1_20260819_104959 \
  trading_corp/brokers/kalshi.py.bak_item2_20260819_103406; do
  if [ -f "$bk" ]; then echo "OK   $bk ($(tr -d '\r' < $bk | md5sum | cut -d' ' -f1))"; else echo "MISSING $bk"; fi
done
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$sh = "printf %s '$b64' | base64 -d | bash"
Write-Host "== SESSION-WRAP VERIFY (READ-ONLY): deployed state + backups =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts $sh --query "value[0].message" -o tsv
