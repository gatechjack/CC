# pk_cp6_rollback.ps1 -- Phase 2 CP6 STAGE 2 ROLLBACK: restore the OLD code + restart, undoing the batch.
#
# Use if step 3 (pk_cp6_restart_verify.ps1) shows the loop did NOT re-arm / engine not online / boot
# tracebacks. Pass the backup suffix printed by pk_cp6_deploy.ps1 (BACKUP_SUFFIX .bak_cp6_<ts>).
#
# CRITICAL ORDERING (why -CutoverWasApplied exists): if step 2 (pk_cutover_seed.ps1 -Apply) already ran,
# it EMPTIED selected_whales and seeded live_whales. The OLD (restored) code reads selected_whales -- so
# restoring old code WITHOUT reversing the cutover leaves the old loop watching NOBODY. With
# -CutoverWasApplied this runner FIRST reverses the cutover (move live_whales back to selected+pinned)
# using a SELF-CONTAINED transaction that does NOT depend on set_agent_state_multi (which the restore
# removes) -- THEN restores the 11 files, removes roster_split.py, and restarts. Run:
#   powershell -ep bypass -f .\pk_cp6_rollback.ps1 -BackupSuffix .bak_cp6_<ts> -CutoverWasApplied
#   powershell -ep bypass -f .\pk_cp6_rollback.ps1 -BackupSuffix .bak_cp6_<ts>            (if cutover NOT yet run)
param([Parameter(Mandatory=$true)][string]$BackupSuffix, [switch]$CutoverWasApplied)
$ErrorActionPreference = 'Stop'
$rev = if ($CutoverWasApplied) { 'YES' } else { 'NO' }
$apply = @'
cd /home/azureuser/trading_corp
SUF="__SUFFIX__"
REVERSE="__REVERSE__"
echo "ROLLBACK suffix=$SUF reverse_cutover=$REVERSE"
if [ "$REVERSE" = "YES" ]; then
venv/bin/python3 - <<'PY'
import os, json, datetime
try:
    from dotenv import load_dotenv; load_dotenv("/home/azureuser/trading_corp/.env")
except Exception:
    pass
DB = os.environ.get("TRADING_CORP_DB_URL", "sqlite:///data/trading_corp.db")
from trading_corp.persistence import db
def wof(x):
    if isinstance(x, dict):
        return (x.get('wallet') or x.get('proxy_wallet') or '').strip().lower()
    return str(x).strip().lower()
with db.connect(DB) as c:
    def load(a, k):
        r = c.execute("select value_json from agent_state where agent=? and key=?", (a, k)).fetchone()
        return json.loads(r[0]) if r and r[0] else []
    live = load('poly_kalshi_mlb', 'live_whales')
    sel = load('polymarket_copy_trader', 'selected_whales')
    pin = load('polymarket_copy_trader', 'pinned_whales')
    sset = {wof(x) for x in sel}; pset = {wof(x) for x in pin}
    sel2 = list(sel) + [x for x in live if wof(x) not in sset]
    pin2 = list(pin) + [x for x in live if wof(x) not in pset]
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    c.execute("BEGIN IMMEDIATE")
    for a, k, v in [('poly_kalshi_mlb', 'live_whales', []), ('polymarket_copy_trader', 'selected_whales', sel2), ('polymarket_copy_trader', 'pinned_whales', pin2)]:
        c.execute("insert into agent_state(agent,key,value_json,updated_ts) values(?,?,?,?) on conflict(agent,key) do update set value_json=excluded.value_json, updated_ts=excluded.updated_ts", (a, k, json.dumps(v, separators=(',', ':')), ts))
    c.execute("COMMIT")
    print("CUTOVER_REVERSED live->0 selected->%d pinned->%d" % (len(sel2), len(pin2)))
PY
fi
echo "== restore 11 modified from $SUF =="
for f in config/strategies.yaml trading_corp/agents/poly_kalshi_marks.py trading_corp/agents/strategies/poly_kalshi_executor.py trading_corp/agents/strategies/polymarket_copy_trader.py trading_corp/main.py trading_corp/persistence/db.py trading_corp/web/data.py trading_corp/web/routes.py trading_corp/web/templates/home.html trading_corp/web/templates/partials/poly_kalshi_live.html trading_corp/web/templates/partials/poly_kalshi_live_inner.html; do if [ -f "${f}${SUF}" ]; then cp "${f}${SUF}" "$f"; echo "RESTORED $f"; else echo "MISSING_BACKUP ${f}${SUF}"; fi; done
rm -f trading_corp/agents/strategies/roster_split.py && echo "REMOVED roster_split.py"
RS=$(date -u +"%Y-%m-%d %H:%M:%S"); echo "RESTART_AT_UTC $RS"
systemctl restart trading-corp
UP=0
for k in $(seq 1 72); do sleep 5; if journalctl -u trading-corp --since "$RS" --no-pager 2>/dev/null | grep -q "MLB copy loop online"; then UP=$((k*5)); break; fi; done
echo "ROLLBACK_ONLINE_SECONDS $UP"
echo "PID $(systemctl show trading-corp -p MainPID --value)"
journalctl -u trading-corp --since "$RS" --no-pager 2>/dev/null | grep -iE "MLB copy loop online|MLB copy WIRED|Traceback|CRITICAL" | head -20
if [ "$UP" -gt 0 ]; then
venv/bin/python3 - <<'PY'
import os
try:
    from dotenv import load_dotenv; load_dotenv("/home/azureuser/trading_corp/.env")
except Exception:
    pass
DB = os.environ.get("TRADING_CORP_DB_URL", "sqlite:///data/trading_corp.db")
from trading_corp.persistence import db
r = db.load_agent_state('polymarket_copy_trader', 'selected_whales', db_url=DB)
v = r[0] if r else []
print("OLD_CODE_ROSTER selected_whales n=%d (old code reads THIS)" % (len(v) if isinstance(v, list) else -1))
PY
else
echo "ROLLBACK_ENGINE_NOT_ONLINE_after_360s -- manual intervention required"
fi
'@
$apply = $apply -replace "__SUFFIX__", $BackupSuffix
$apply = $apply -replace "__REVERSE__", $rev
$apply = $apply -replace "`r", ""
$ab64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($apply))
$acmd = "printf %s '$ab64' | base64 -d | bash"
Write-Host "== CP6 ROLLBACK (reverse-cutover=$rev -> restore 11 -> rm roster_split.py -> restart) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts $acmd --query "value[0].message" -o tsv
