#!/usr/bin/env bash
# CP1 post-apply READ-ONLY re-verify: (B) re-check single_game_pct NULL on the LIVE DB for fed + unknown (the
# Stage-2 [6] query had a missing binding) and NON-null for a sports category (nba); (A) snapshot deployed code +
# DB + parent-dir ownership/mode. READ-ONLY: mode=ro DB read + ls only. No writes, no chown, no engine touch.
echo "=== PM P2 CP1 POST-VERIFY (read-only) ==="
date -u
echo "whoami=$(whoami)"
H="${HOME:-/home/azureuser}"
ROOT="$H/trading_corp"
LIVE="$ROOT/data/prediction_markets.db"
PKG="$ROOT/trading_corp/prediction_markets"
SCR="$ROOT/trading_corp/scripts"
VP="$ROOT/venv/bin/python"

echo ""
echo "=== (B) single_game_pct NULL for fed + unknown on LIVE (corrected query) ==="
"$VP" -c "import sqlite3
c=sqlite3.connect('file:$LIVE?mode=ro',uri=True); q=c.cursor()
print('schema_version', q.execute('SELECT MAX(version) FROM schema_version').fetchone()[0], '(expect 4)')
for c2 in ('fed','unknown'): print(' ',c2,'rows',q.execute('SELECT COUNT(*) FROM pm_category_stats WHERE category=?',(c2,)).fetchone()[0],'single_game_pct_NULL',q.execute('SELECT COUNT(*) FROM pm_category_stats WHERE category=? AND single_game_pct IS NULL',(c2,)).fetchone()[0],'(expect equal)')
print('fed per-wallet single_game_pct (expect None):')
[print('  ',w[:16],'sgp=',sgp) for w,sgp in q.execute('SELECT wallet,single_game_pct FROM pm_category_stats WHERE category=?',('fed',)).fetchall()]
print('nba sample single_game_pct (expect NON-null real values):')
[print('  ',w[:16],'sgp=',sgp) for w,sgp in q.execute('SELECT wallet,single_game_pct FROM pm_category_stats WHERE category=? ORDER BY n_condition_ids DESC LIMIT 3',('nba',)).fetchall()]
c.close()" 2>&1

echo ""
echo "=== (A) deployed code + DB + parent-dir ownership/mode (read-only ls) ==="
echo "-- code files (found root:root 666 in Stage 2; document current state) --"
ls -l "$PKG/db.py" "$PKG/stats.py" "$PKG/category.py" "$SCR/pm_cli.py" 2>&1
echo "-- parent dirs (azureuser-owned dir => azureuser could rm+recopy; root-owned => root step needed) --"
ls -ld "$PKG" "$SCR" 2>&1
echo "-- live DB (expect azureuser:azureuser 644) --"
ls -l "$LIVE" 2>&1
echo "=== POST-VERIFY done ==="
