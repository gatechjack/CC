# pk_item1_verify_ro.ps1 -- READ-ONLY post-deploy verify for Item 1. NO writes.
# armed status, both deployed files + 3 byte-locked LF-md5, [G-conflict] code path
# present, boot WIRED/roster/tracebacks, and any skip_conflict/skip_gate_error audit
# rows (gate firing). Run: powershell -ep bypass -f .\pk_item1_verify_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
echo "MainPID = $(systemctl show -p MainPID --value trading-corp 2>/dev/null)"
AE=$(systemctl show -p ActiveEnterTimestamp --value trading-corp 2>/dev/null); echo "engine ActiveEnter = $AE"
echo "=== deployed 2 files (expect exec 257f6433 / match 7c191e83) + 3 byte-locked LF-md5 ==="
for f in trading_corp/agents/strategies/poly_kalshi_executor.py trading_corp/data/mlb_poly_kalshi_match.py trading_corp/agents/strategies/kalshi_copy_trader.py trading_corp/data/sports_team_mapping.py trading_corp/brokers/kalshi_live.py; do
  printf "%-58s " "$f"; tr -d '\r' < "$f" | md5sum | cut -d' ' -f1
done
echo "=== [G-conflict] code path present on the box ==="
echo "executor [G-conflict] lines: $(grep -c 'G-conflict' trading_corp/agents/strategies/poly_kalshi_executor.py)"
echo "executor has _opposite_side_on_game / skip_gate_error / skip_conflict:"
grep -oE '_opposite_side_on_game|skip_gate_error|skip_conflict' trading_corp/agents/strategies/poly_kalshi_executor.py | sort | uniq -c
echo "matcher has game_key_and_side def: $(grep -c 'def game_key_and_side' trading_corp/data/mlb_poly_kalshi_match.py)"
echo "=== armed ==="
venv/bin/python3 - <<'PY'
import os, yaml
try:
    from dotenv import load_dotenv; load_dotenv("/home/azureuser/trading_corp/.env")
except Exception:
    pass
cfg = (yaml.safe_load(open("config/strategies.yaml")) or {}).get("poly_kalshi_mlb") or {}
auto = bool(cfg.get("auto_execute", False))
from trading_corp.persistence.models import StrategyState
DB = os.environ.get("TRADING_CORP_DB_URL", "sqlite:///data/trading_corp.db")
print("auto_execute =", auto, " dry_run =", (not auto),
      " halted =", StrategyState.from_persistence("poly_kalshi_mlb", db_url=DB).halted)
from trading_corp.persistence import db
with db.connect(DB) as c:
    for st in ("skip_conflict", "skip_gate_error"):
        n = c.execute("select count(*) from audit_event where actor='poly_kalshi_mlb' "
                      "and kind='poly_kalshi_order' and json_extract(payload_json,'$.status')=?", (st,)).fetchone()[0]
        print("audit_event %-16s rows (lifetime) = %s" % (st, n))
PY
echo "=== boot: WIRED + roster invariant (most recent) ==="
journalctl -u trading-corp --no-pager -o cat 2>/dev/null | grep -E "Poly->Kalshi MLB copy WIRED|roster invariant" | tail -2
echo "tracebacks since engine start: $(journalctl -u trading-corp --since "$AE" --no-pager 2>/dev/null | grep -c 'Traceback (most recent call last)')"
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$sh = "printf %s '$b64' | base64 -d | bash"
Write-Host "== ITEM 1 POST-DEPLOY VERIFY (READ-ONLY) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts $sh --query "value[0].message" -o tsv
