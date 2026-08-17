# pk_cp6_driftgate_ro.ps1 -- Phase 2 CP6 STAGE 1 (READ-ONLY): drift-gate for the ONE batched deploy.
#
# Confirms each of the 11 MODIFIED deploy files on the box == the 3706a3a baseline (the ACTUAL running
# box = Phase 2b state; NOT prod-live git 18db30e, which lags the box by all of Phase 2b), the 1 NEW
# file (roster_split.py) is ABSENT, the agent_state + Phase-2b mark tables already exist (this batch is
# additive -- NO new migration), and reports engine PID + open-position count (flag-3 context) + the
# current roster keys (cutover precondition: selected_whales should hold the 4, live_whales empty).
#
# md5 is LF-normalized (tr -d CR) so the CRLF main.py compares cleanly against the git-LF baseline; the
# other 10 files are LF on the box (RAW == LF). NO writes, NO restart, NO cutover. Run:
#   powershell -ep bypass -f .\pk_cp6_driftgate_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
echo "== MODIFIED (11): box LF-md5 vs 3706a3a baseline (all must MATCH) =="
ck() { cur=$(tr -d '\r' < "$1" 2>/dev/null | md5sum | cut -d" " -f1); if [ "$cur" = "$2" ]; then s=MATCH; else s=DRIFT; fi; printf "%-6s %s  cur=%s\n" "$s" "$1" "$cur"; }
ck config/strategies.yaml ec8684da6911f0d79c08148bab07d518
ck trading_corp/agents/poly_kalshi_marks.py 887f1bb09610a7c301dc3fc9060b37cc
ck trading_corp/agents/strategies/poly_kalshi_executor.py 3ad0824666d5d89435c7b614a5ff1872
ck trading_corp/agents/strategies/polymarket_copy_trader.py 49d3a5d01280e02d7761bd66957f7eec
ck trading_corp/main.py 229693a8b5a2dd809f6e8825b667cb80
ck trading_corp/persistence/db.py 9daf8bf6474f3fef712bbf217d7ab3a1
ck trading_corp/web/data.py 36180479f3df051ff43ce5f496bfd7dd
ck trading_corp/web/routes.py 589291482a32911e41229b60680fcd2e
ck trading_corp/web/templates/home.html 31589243c9e8f92a0d8cfd7eb0c2d176
ck trading_corp/web/templates/partials/poly_kalshi_live.html 176d102c3c867890d35fdaeeb5e7db03
ck trading_corp/web/templates/partials/poly_kalshi_live_inner.html 69267e6dae67b345c53e00aa09545581
echo "== NEW (1): roster_split.py must be ABSENT on the box today =="
f=trading_corp/agents/strategies/roster_split.py; if [ -f "$f" ]; then echo "PRESENT_UNEXPECTED $f"; else echo "ABSENT_OK $f"; fi
echo "== migration: agent_state + Phase-2b mark tables already present (batch additive, NO new migration) =="
echo "ENGINE_PID $(systemctl show trading-corp -p MainPID --value)"
venv/bin/python3 - <<'PY'
import os
try:
    from dotenv import load_dotenv; load_dotenv("/home/azureuser/trading_corp/.env")
except Exception:
    pass
DB = os.environ.get("TRADING_CORP_DB_URL", "sqlite:///data/trading_corp.db")
from trading_corp.persistence import db
with db.connect(DB) as c:
    tabs = sorted(r[0] for r in c.execute(
        "select name from sqlite_master where type='table' and "
        "name in ('agent_state','poly_kalshi_mark_live','poly_kalshi_mark_history')"))
    print("EXISTING_TABLES(expect all 3)", tabs)
    n = c.execute(
        "select count(*) from audit_event a "
        "left join kalshi_round_trips r on r.order_id=json_extract(a.payload_json,'$.order_id') "
        "where a.actor='poly_kalshi_mlb' and a.kind='poly_kalshi_order' "
        "and json_extract(a.payload_json,'$.status')='placed' "
        "and coalesce(json_extract(a.payload_json,'$.order_id'),'')!='' and r.order_id is null").fetchone()[0]
    print("OPEN_POSITIONS(flag-3 context)", n)
    def w(actor, key):
        rec = db.load_agent_state(actor, key, db_url=DB)
        v = rec[0] if rec else None
        if isinstance(v, list):
            return [((x.get('wallet') or x.get('proxy_wallet')) if isinstance(x, dict) else x) for x in v]
        return v
    print("CUTOVER_PRECOND selected_whales", w('polymarket_copy_trader', 'selected_whales'))
    print("CUTOVER_PRECOND pinned_whales  ", w('polymarket_copy_trader', 'pinned_whales'))
    print("CUTOVER_PRECOND live_whales    ", w('poly_kalshi_mlb', 'live_whales'))
PY
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$sh = "printf %s '$b64' | base64 -d | bash"
Write-Host "== Phase 2 CP6 STAGE 1 drift-gate (READ-ONLY; no writes, no restart, no cutover) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts $sh --query "value[0].message" -o tsv
