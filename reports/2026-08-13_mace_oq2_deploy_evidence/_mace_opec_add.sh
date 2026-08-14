#!/bin/bash
# OPEC blackout row add (operator-ratified 2026-08-14) + end-to-end verification.
# Writes: ONE economic_event row (OPEC / 2026-09-07 / scope ALL / source manual).
# 2026-09-07 = the 2026-09-06 SUNDAY meeting weekend-rolled to Monday, per the
# system's own LPR_FIX storage convention (is_blackout matches exact weekday dates).
set -u
R=/home/azureuser/trading_corp
cd "$R" || { echo "RESULT: FAILED - no repo dir"; exit 1; }
echo "=== STEP 1: CLI add ==="
runuser -u azureuser -- venv/bin/python scripts/mace_calendar_cli.py add --type OPEC --date 2026-09-07 --scope ALL
rc=$?
[ $rc -eq 0 ] || { echo "RESULT: FAILED - add rc=$rc"; exit 1; }
echo ""
echo "=== STEP 2: OPEC rows on record ==="
runuser -u azureuser -- venv/bin/python scripts/mace_calendar_cli.py list --type OPEC
echo ""
echo "=== STEP 3: end-to-end gate proof (deployed code + live DB, read-only) ==="
runuser -u azureuser -- venv/bin/python - <<'PYEOF'
import sqlite3
from datetime import date
from trading_corp.mace.config import load_mace_config
from trading_corp.mace import strategy as st

cfg = load_mace_config()
sc = cfg.symbols['XLE']
print('XLE blackout_event_types:', list(sc.blackout_event_types))

conn = sqlite3.connect('file:data/trading_corp.db?mode=ro', uri=True)
conn.row_factory = sqlite3.Row
events = [{'event_type': r['event_type'], 'symbol_scope': r['symbol_scope'],
           'event_date': r['event_date']}
          for r in conn.execute('SELECT event_type, symbol_scope, event_date FROM economic_event')]
print('events loaded:', len(events))

d = date(2026, 9, 4)                       # Friday eval before the Sunday meeting
ns = st.next_session(d)
print('eval 2026-09-04 window: {%s, %s}' % (d.isoformat(), ns.isoformat()))
hit = st.is_blackout('XLE', sc, events, d, ns)
print('is_blackout XLE @ 2026-09-04 eval:', hit, '->', 'PASS (gate FIRES)' if hit else 'FAIL')

d2 = date(2026, 9, 8)                      # Tuesday after Labor Day - event passed
hit2 = st.is_blackout('XLE', sc, events, d2, st.next_session(d2))
print('is_blackout XLE @ 2026-09-08 eval:', hit2, '->', 'PASS (re-eligible, event passed)' if not hit2 else 'UNEXPECTED')

fake = [{'event_type': 'OPEC', 'symbol_scope': 'ALL', 'event_date': '2026-09-06'}]
inert = st.is_blackout('XLE', sc, fake, d, ns)
print('counterfactual raw Sunday 2026-09-06 row @ 9/4 eval:', inert,
      '->', 'CONFIRMED INERT (weekend date can never match)' if not inert else 'UNEXPECTED')

gdx = st.is_blackout('GDX', cfg.symbols['GDX'], events, date(2026, 9, 15),
                     st.next_session(date(2026, 9, 15)))
print('bonus: is_blackout GDX @ 2026-09-15 eval (FOMC 9/16):', gdx,
      '->', 'PASS' if gdx else 'FAIL')
PYEOF
echo ""
echo "RESULT: OK"
