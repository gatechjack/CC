# pk_mark_investigation_ro.ps1 -- READ-ONLY: has the poly_kalshi mark poller EVER marked a live position?
# Pulls (1) current mark_live/mark_history contents + the open positions' mark status, and (2) the
# DECISIVE journal signal: the "poly_kalshi mark tick: open=N marked=M quote_miss=Q" distribution
# (marked ever >0?), the pre-CP6 TypeError ticks (context), and any "Kalshi quote failed" warnings.
# No writes. Run:
#   powershell -ep bypass -f .\pk_mark_investigation_ro.ps1
$ErrorActionPreference = 'Stop'
$bash = @'
cd /home/azureuser/trading_corp
echo "=== 1. MARK TABLES NOW ==="
venv/bin/python3 - <<'PY'
import os
try:
    from dotenv import load_dotenv; load_dotenv("/home/azureuser/trading_corp/.env")
except Exception:
    pass
DB = os.environ.get("TRADING_CORP_DB_URL", "sqlite:///data/trading_corp.db")
from trading_corp.persistence import db
with db.connect(DB) as c:
    live = c.execute("select order_id,ticker,yes_mid,unrealized,mark_ts from poly_kalshi_mark_live order by mark_ts").fetchall()
    print("mark_live rows =", len(live))
    for r in live:
        print("   LIVE", tuple(r))
    h = c.execute("select count(*), count(distinct order_id) from poly_kalshi_mark_history").fetchone()
    print("mark_history total_points =", h[0], " distinct_order_ids =", h[1])
    for r in c.execute("select order_id,count(*),min(ts),max(ts),min(yes_mid),max(yes_mid) from poly_kalshi_mark_history group by order_id"):
        print("   HIST", tuple(r))
    opens = c.execute("select json_extract(payload_json,'$.order_id'), json_extract(payload_json,'$.ticker') from audit_event where actor='poly_kalshi_mlb' and kind='poly_kalshi_order' and json_extract(payload_json,'$.status')='placed' and coalesce(json_extract(payload_json,'$.action'),'entry')='entry' and coalesce(json_extract(payload_json,'$.order_id'),'')!='' and json_extract(payload_json,'$.order_id') not in (select order_id from kalshi_round_trips where order_id is not null)").fetchall()
    print("OPEN positions now =", len(opens))
    for oid, tk in opens:
        m = c.execute("select yes_mid,mark_ts from poly_kalshi_mark_live where order_id=?", (oid,)).fetchone()
        print("   OPEN", oid, tk, "->", (tuple(m) if m else "NO_MARK"))
PY
echo ""
echo "=== 2. DECISIVE: poller tick log (post-CP6 clean log). marked distribution across ALL cycles ==="
echo "total 'mark tick' lines : $(journalctl -u trading-corp --no-pager 2>/dev/null | grep -c 'poly_kalshi mark tick')"
echo "marked= value histogram (count of each marked=N):"
journalctl -u trading-corp --no-pager 2>/dev/null | grep 'poly_kalshi mark tick' | grep -oE 'marked=[0-9]+' | sort | uniq -c
echo "quote_miss= value histogram:"
journalctl -u trading-corp --no-pager 2>/dev/null | grep 'poly_kalshi mark tick' | grep -oE 'quote_miss=[0-9]+' | sort | uniq -c
echo "-- ANY tick with marked>0 (the smoking gun -- empty = NEVER marked): --"
journalctl -u trading-corp --no-pager 2>/dev/null | grep 'poly_kalshi mark tick' | grep -E 'marked=[1-9]' | head -20
echo "-- earliest + latest tick line (evidence window coverage): --"
journalctl -u trading-corp --no-pager 2>/dev/null | grep 'poly_kalshi mark tick' | head -1
journalctl -u trading-corp --no-pager 2>/dev/null | grep 'poly_kalshi mark tick' | tail -1
echo ""
echo "=== 3. quote() failure path: 'Kalshi quote failed' warnings ==="
echo "count: $(journalctl -u trading-corp --no-pager 2>/dev/null | grep -c 'Kalshi quote failed')"
journalctl -u trading-corp --no-pager 2>/dev/null | grep 'Kalshi quote failed' | tail -6
echo ""
echo "=== 4. context: pre-CP6 mark-tick TypeError cycles (the 8dc4d97 fix window) ==="
echo "count 'mark tick error': $(journalctl -u trading-corp --no-pager 2>/dev/null | grep -c 'mark tick error')"
echo "journal earliest entry: $(journalctl -u trading-corp --no-pager -n 1 -o short-iso 2>/dev/null | head -1 | cut -d' ' -f1)"
'@
$bash = $bash -replace "`r", ""
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
$sh = "printf %s '$b64' | base64 -d | bash"
Write-Host "== poly_kalshi mark-poller investigation (READ-ONLY): has it EVER marked a live position? =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts $sh --query "value[0].message" -o tsv
