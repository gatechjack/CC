# pk_cp6_restart_verify.ps1 -- Phase 2 CP6 STAGE 2, step 3 of 3: RESTART onto the new code + seeded
# roster, then full verify. Run ONLY after step 1 (pk_cp6_deploy.ps1 install) AND step 2
# (pk_cutover_seed.ps1 -Apply, live_whales seeded). This restarts the ARMED live loop.
#
# If the loop does NOT re-arm / the engine does not come online -> run:
#   powershell -ep bypass -f .\pk_cp6_rollback.ps1 -BackupSuffix <suffix> -CutoverWasApplied
# Run:
#   powershell -ep bypass -f .\pk_cp6_restart_verify.ps1
$ErrorActionPreference = 'Stop'
$apply = @'
cd /home/azureuser/trading_corp
RS=$(date -u +"%Y-%m-%d %H:%M:%S"); echo "RESTART_AT_UTC $RS"
systemctl restart trading-corp
UP=0
for k in $(seq 1 72); do sleep 5; if journalctl -u trading-corp --since "$RS" --no-pager 2>/dev/null | grep -q "MLB copy loop online"; then UP=$((k*5)); break; fi; done
echo "POLY_KALSHI_ONLINE_SECONDS $UP"
echo "PID $(systemctl show trading-corp -p MainPID --value)"
echo "=== boot journal (armed / retarget / invariant / poller / errors) ==="
journalctl -u trading-corp --since "$RS" --no-pager 2>/dev/null | grep -iE "MLB copy loop online|MLB copy WIRED|roster invariant|mark poller|Traceback|CRITICAL" | head -40
PT=$(journalctl -u trading-corp --since "$RS" --no-pager 2>/dev/null | grep -cE "Polymarket copy (ENTRY|EXIT)")
echo "PAPER_TELEGRAM_CARDS_SINCE_RESTART(expect 0) $PT"
if [ "$UP" -gt 0 ]; then
sleep 75
venv/bin/python3 - <<'PY'
import os
try:
    from dotenv import load_dotenv; load_dotenv("/home/azureuser/trading_corp/.env")
except Exception:
    pass
DB = os.environ.get("TRADING_CORP_DB_URL", "sqlite:///data/trading_corp.db")
from trading_corp.persistence import db
from trading_corp.persistence.models import StrategyState
from trading_corp.agents.strategies.roster_split import extract_wallets, check_rosters_disjoint
import yaml
cfg = (yaml.safe_load(open("config/strategies.yaml")) or {}).get("poly_kalshi_mlb", {})
print("CONFIG_RETARGET roster_actor=%s roster_key=%s" % (cfg.get("roster_actor"), cfg.get("roster_key")))
def w(a, k):
    r = db.load_agent_state(a, k, db_url=DB); v = r[0] if r else None
    return sorted(extract_wallets(v)) if isinstance(v, list) else v
lw = w('poly_kalshi_mlb', 'live_whales'); sw = w('polymarket_copy_trader', 'selected_whales'); pw = w('polymarket_copy_trader', 'pinned_whales')
print("LIVE_WHALES n=%s %s" % (len(lw) if isinstance(lw, list) else lw, lw))
print("SELECTED_WHALES(expect 0) n=%s %s" % (len(sw) if isinstance(sw, list) else sw, sw))
print("PINNED_WHALES(expect 0) n=%s %s" % (len(pw) if isinstance(pw, list) else pw, pw))
try:
    live, paper = check_rosters_disjoint(db_url=DB)
    print("INVARIANT_OK live=%d paper=%d disjoint" % (len(live), len(paper)))
except Exception as e:
    print("INVARIANT_VIOLATED", e)
ss = StrategyState.from_persistence("poly_kalshi_mlb", db_url=DB)
print("ARM halted=%s reason=%s (expect halted=False)" % (ss.halted, ss.halt_reason))
with db.connect(DB) as c:
    openn = c.execute("select count(*) from audit_event a left join kalshi_round_trips r on r.order_id=json_extract(a.payload_json,'$.order_id') where a.actor='poly_kalshi_mlb' and a.kind='poly_kalshi_order' and json_extract(a.payload_json,'$.status')='placed' and coalesce(json_extract(a.payload_json,'$.order_id'),'')!='' and r.order_id is null").fetchone()[0]
    print("OPEN_POSITIONS(flag-3 expect 1, no re-order)", openn)
try:
    from trading_corp.web.data import build_poly_kalshi_live_view
    v = build_poly_kalshi_live_view(DB)
    print("DASHBOARD n_open", v.n_open, "total_unrealized", v.total_unrealized)
    for p in v.open_positions:
        print("DASHBOARD_POS", p.order_id, p.ticker, "yes_mid", p.yes_mid, "unrealized", p.unrealized, "stale", p.stale)
except Exception as e:
    print("DASHBOARD_VIEW_ERR", e)
PY
else
echo "ENGINE_NOT_ONLINE_after_360s -- run pk_cp6_rollback.ps1 -BackupSuffix <suffix> -CutoverWasApplied"
fi
'@
$apply = $apply -replace "`r", ""
$ab64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($apply))
$acmd = "printf %s '$ab64' | base64 -d | bash"
Write-Host "== CP6 STAGE 2 step 3 RESTART + VERIFY (restarts the armed loop; waits ~2-7 min) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts $acmd --query "value[0].message" -o tsv
