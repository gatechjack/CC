#!/bin/bash
# MACE calendar refresh (idempotent INSERT OR IGNORE seed) + verification.
# Writes: economic_event table ONLY (no code, no config, no restart).
set -u
R=/home/azureuser/trading_corp
cd "$R" || { echo "RESULT: FAILED - no repo dir"; exit 1; }
echo "=== STEP 1: CLI refresh (as azureuser) ==="
runuser -u azureuser -- venv/bin/python scripts/mace_calendar_cli.py refresh
rc=$?
[ $rc -eq 0 ] || { echo "RESULT: FAILED - refresh rc=$rc"; exit 1; }
echo ""
echo "=== STEP 2: forward window per CLI list (2026-08-14..2026-09-30) ==="
runuser -u azureuser -- venv/bin/python scripts/mace_calendar_cli.py list --from 2026-08-14 --to 2026-09-30
echo ""
echo "=== STEP 3: DB verify (read-only) ==="
runuser -u azureuser -- venv/bin/python - <<'PYEOF'
import sqlite3
conn = sqlite3.connect('file:/home/azureuser/trading_corp/data/trading_corp.db?mode=ro', uri=True)
conn.row_factory = sqlite3.Row
print('TOTAL rows:', conn.execute('SELECT COUNT(*) c FROM economic_event').fetchone()['c'])
print('--- BY TYPE+SOURCE ---')
for r in conn.execute('SELECT event_type t, source s, COUNT(*) n, MIN(event_date) lo, MAX(event_date) hi FROM economic_event GROUP BY 1,2 ORDER BY 1,2'):
    print(f"{r['t']:8s} {r['s']:5s} n={r['n']:3d} {r['lo']}..{r['hi']}")
print('--- NEXT PER TYPE (>= 2026-08-14) ---')
for r in conn.execute("SELECT event_type t, MIN(event_date) nxt FROM economic_event WHERE event_date >= '2026-08-14' GROUP BY 1 ORDER BY 2"):
    print(f"{r['t']:8s} next {r['nxt']}")
ck = [('LPR_FIX','2026-08-20'), ('FOMC','2026-09-16'), ('CPI','2026-09-11'), ('NFP','2026-09-04')]
for t, d in ck:
    hit = conn.execute("SELECT 1 FROM economic_event WHERE event_type=? AND event_date=?", (t, d)).fetchone()
    print(f"CHECK {t:8s} {d}: {'PASS' if hit else 'FAIL'}")
n = conn.execute("SELECT COUNT(*) c FROM audit_event WHERE kind LIKE 'mace_calendar_refresh%'").fetchone()['c']
print(f"mace_calendar_refresh audit rows: {n} (CLI path does not audit; expected 0 today - ANY row Monday = Sunday loop ran)")
PYEOF
echo ""
echo "=== STEP 4: /mace panel render (next-7d) ==="
H=$(curl -s --max-time 20 http://127.0.0.1:8000/mace)
[ -n "$H" ] || { echo "RESULT: FAILED - /mace empty response"; exit 1; }
if echo "$H" | grep -q 'no events in the next 7 days'; then echo 'PANEL: EMPTY (UNEXPECTED)'; else echo 'PANEL: honest-empty line GONE (has events)'; fi
echo "$H" | grep -q 'LPR_FIX'    && echo 'PANEL: LPR_FIX rendered'    || echo 'PANEL: LPR_FIX NOT rendered (UNEXPECTED)'
echo "$H" | grep -q '2026-08-20' && echo 'PANEL: 2026-08-20 rendered' || echo 'PANEL: 2026-08-20 NOT rendered (UNEXPECTED)'
echo ""
echo "RESULT: OK"
