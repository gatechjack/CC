#!/bin/bash
# READ-ONLY: economic_event blackout-calendar inspection (mode=ro URI, no writes).
set -u
R=/home/azureuser/trading_corp
PY=$R/venv/bin/python
cd "$R" || { echo "RESULT: FAILED - no repo dir"; exit 1; }
runuser -u azureuser -- "$PY" - <<'PYEOF'
import sqlite3
conn = sqlite3.connect('file:/home/azureuser/trading_corp/data/trading_corp.db?mode=ro', uri=True)
conn.row_factory = sqlite3.Row
try:
    tot = conn.execute('SELECT COUNT(*) c FROM economic_event').fetchone()['c']
except Exception as e:
    print('TABLE READ FAILED:', e)
    raise SystemExit(0)
print('TOTAL economic_event rows:', tot)
print('--- BY TYPE+SOURCE (n, min..max date) ---')
for r in conn.execute('SELECT event_type t, source s, COUNT(*) n, MIN(event_date) lo, MAX(event_date) hi FROM economic_event GROUP BY 1,2 ORDER BY 1,2'):
    print(f"{r['t']:12s} {r['s']:7s} n={r['n']:3d} {r['lo']}..{r['hi']}")
print('--- NEXT OCCURRENCE PER TYPE (>= 2026-08-14) ---')
for r in conn.execute("SELECT event_type t, MIN(event_date) nxt FROM economic_event WHERE event_date >= '2026-08-14' GROUP BY 1 ORDER BY 2"):
    print(f"{r['t']:12s} next {r['nxt']}")
print('--- NEXT 12 UPCOMING ROWS ---')
for r in conn.execute("SELECT event_type t, symbol_scope sc, event_date d, source s FROM economic_event WHERE event_date >= '2026-08-14' ORDER BY event_date LIMIT 12"):
    print(f"{r['d']}  {r['t']:12s} scope={r['sc']:4s} src={r['s']}")
print('--- mace_calendar_refresh AUDITS (latest 5) ---')
n = 0
for r in conn.execute("SELECT ts, kind, payload_json FROM audit_event WHERE kind LIKE 'mace_calendar_refresh%' ORDER BY ts DESC LIMIT 5"):
    n += 1
    print(r['ts'], r['kind'], (r['payload_json'] or '')[:200])
if n == 0:
    print('(none - weekly refresh has NEVER run)')
print('RESULT: OK')
PYEOF
