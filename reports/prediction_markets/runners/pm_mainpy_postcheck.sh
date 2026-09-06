set -u
ROOT=/home/azureuser/trading_corp
PMDB=$ROOT/data/prediction_markets.db
LDB=$ROOT/data/trading_corp.db
V=$ROOT/venv/bin/python
ST=$(systemctl show -p ActiveEnterTimestamp --value trading-corp 2>/dev/null | sed 's/^[A-Za-z]* //')
echo "### PM DRIVER RESTORE -- POST-CHECK (after Jack's restart) since=$ST ###"
echo "engine PID=$(systemctl show -p MainPID --value trading-corp 2>/dev/null) NRestarts=$(systemctl show -p NRestarts --value trading-corp 2>/dev/null)"
echo
echo "## [1] THE ROSTER LINE (the headline; its ABSENCE was this whole incident) -- expect one task/account, [atp,mlb,ufc,wta]:"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -E 'PM LIVE DRIVER WIRED|PM live driver' | grep -ivE 'IBIT|Candle' | sed 's/^/  /' | tail -6
echo
echo "## [2] BOOT-RECONCILE verdict, both accounts (whatever it is -- a LATCH here is CORRECT, not a fault):"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -iE 'boot.?reconcil|reconcile_account|latched_categories|reconciled=' | grep -iE 'kalshi_jack|kalshi_karen|pm|account' | grep -ivE 'IBIT|Candle|coinbase|bitunix' | sed 's/^/  /' | tail -12
echo
echo "## [3] LATCH state (REPORT ONLY -- do NOT clear; Jack clears once he has seen what it latched on):"
$V - <<PY 2>/dev/null || echo "  (arm read failed)"
import sqlite3,json
c=sqlite3.connect("file:$LDB?mode=ro", uri=True)
rows=c.execute("SELECT key,value_json FROM agent_state WHERE key LIKE 'arm:%' ORDER BY key").fetchall()
lat=[k for k,v in rows if json.loads(v).get('latched')]
print("  latched arm rows:", lat if lat else "NONE")
print("  arm rows armed=True:", sum(1 for k,v in rows if json.loads(v).get('armed') is True), "of", len(rows), "(expect 9/9)")
for k,v in rows:
    d=json.loads(v); print("   ", k, "armed=%s latched=%s ts=%s" % (d.get('armed'),d.get('latched'),d.get('ts') or d.get('updated_ts')))
PY
echo
echo "## [4] THE 12 -- R-d catch-up, WALKED INDIVIDUALLY (largest batch it has ever booked; do NOT report only a total):"
$V - <<PY 2>/dev/null || echo "  (settlement walk failed)"
import sqlite3
from trading_corp.prediction_markets import subdivision
c=sqlite3.connect("file:$PMDB?mode=ro", uri=True); c.row_factory=sqlite3.Row
SUBS=[("kalshi_jack","mlb"),("kalshi_jack","atp"),("kalshi_jack","wta"),
      ("kalshi_karen","mlb"),("kalshi_karen","atp"),("kalshi_karen","wta")]
still=0
for a,cat in SUBS:
    p=subdivision.live_positions(c,a,cat); still+=len(p)
    if p: print("  STILL-OPEN %s/%s: %d -> %s" % (a,cat,len(p),[x['ticker'] for x in p]))
print("  still-open across the 6 = %d (expect 0 once R-d has booked all 12; R-d scans on a ~600s throttle -> re-run if <12 booked)" % still)
print("  settlement-close rows booked (is_exit=1, close_source='settlement'), each individually:")
rows=c.execute("SELECT id,account_id,category,ticker,won,realized_pnl,close_source FROM pm_subdivision_order "
               "WHERE is_exit=1 AND close_source='settlement' ORDER BY id DESC LIMIT 20").fetchall()
tot=0.0
for r in rows[::-1]:
    d=dict(r); rp=d.get('realized_pnl') or 0.0; tot+=rp
    print("    id=%s %s/%s %-40s won=%s realized=%+.2f" % (d['id'],d['account_id'],d['category'],d['ticker'],d['won'],rp))
print("  (walk the 12 target tickers among these; confirm each realized value matches its finalized Kalshi result)")
PY
echo
echo "## [5] EVERY OTHER DIVISION back (MACE included -- we modified the file MACE deployed, confirm THEIR health):"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -E 'MACE .* WIRED|Poly->Kalshi MLB copy WIRED|bitunix_sfp observer wired|PEAD wired|Donchian|Tail-Price' | grep -ivE 'IBIT|Candle' | sed 's/^/  /' | tail -10
echo "  errors since restart: $(journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -icE 'Traceback|CRITICAL|wiring FAILED')"
echo
echo "## [6] SUBSEQUENT CYCLES -- is the driver actually evaluating/placing (re-run a few min later):"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -E 'prediction_markets|pm_live|placed|skip:|no_matcher' | grep -ivE 'IBIT|Candle' | sed 's/^/  /' | tail -8
echo "### POST-CHECK DONE ###"
