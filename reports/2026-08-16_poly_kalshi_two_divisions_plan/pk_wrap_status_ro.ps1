# pk_wrap_status_ro.ps1 -- READ-ONLY session-wrap confirmation: engine PID + armed state + rosters +
# invariant + monkeymashingkeyboard's open live positions (ride-on-demote intact + marking). No writes.
#   powershell -ep bypass -f .\pk_wrap_status_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
echo "PID $(systemctl show trading-corp -p MainPID --value)  ACTIVE $(systemctl is-active trading-corp)"
echo "-- last arm/loop-online journal lines --"
journalctl -u trading-corp --no-pager 2>/dev/null | grep -E "MLB copy WIRED|MLB copy loop online" | tail -2
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
ss = StrategyState.from_persistence("poly_kalshi_mlb", db_url=DB)
print("ARM halted=%s reason=%s (armed == halted False)" % (ss.halted, ss.halt_reason))
def w(a, k):
    r = db.load_agent_state(a, k, db_url=DB); v = r[0] if r else None
    return sorted(extract_wallets(v)) if isinstance(v, list) else v
print("live_whales   :", w("poly_kalshi_mlb", "live_whales"))
print("selected(paper):", w("polymarket_copy_trader", "selected_whales"))
try:
    l, p = check_rosters_disjoint(db_url=DB); print("INVARIANT_OK live=%d paper=%d disjoint" % (len(l), len(p)))
except Exception as e:
    print("INVARIANT_VIOLATED", e)
MK = "0x684baa57c338c2549aec0aa3f034f695d72a8409"
with db.connect(DB) as c:
    rows = c.execute("select json_extract(payload_json,'$.order_id'), json_extract(payload_json,'$.ticker') from audit_event where actor='poly_kalshi_mlb' and kind='poly_kalshi_order' and json_extract(payload_json,'$.status')='placed' and coalesce(json_extract(payload_json,'$.action'),'entry')='entry' and coalesce(json_extract(payload_json,'$.order_id'),'')!='' and lower(json_extract(payload_json,'$.whale_wallet'))=? and json_extract(payload_json,'$.order_id') not in (select order_id from kalshi_round_trips where order_id is not null)", (MK,)).fetchall()
    print("monkeymashingkeyboard OPEN live positions n=%d (still live-owned; ride-to-settlement)" % len(rows))
    for oid, tk in rows:
        m = c.execute("select yes_mid, mark_ts from poly_kalshi_mark_live where order_id=?", (oid,)).fetchone()
        print("   open %s %s | mark yes_mid=%s mark_ts=%s" % (oid, tk, (m[0] if m else None), (m[1] if m else "NO_MARK_YET")))
PY
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$sh = "printf %s '$b64' | base64 -d | bash"
Write-Host "== session-wrap status (READ-ONLY): PID / arm / rosters / invariant / monkey open positions =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts $sh --query "value[0].message" -o tsv
