set -u
ROOT=/home/azureuser/trading_corp
PKG=$ROOT/trading_corp
DB=$ROOT/data/prediction_markets.db
V=$ROOT/venv/bin/python
echo "### FARM-SEARCH DEPLOY RECON (READ-ONLY; nothing written/restarted) $(date -u +%Y%m%dT%H%M%SZ) ###"
echo "engine PID: $(systemctl show -p MainPID --value trading-corp 2>/dev/null) | pm_web PID: $(systemctl show -p MainPID --value prediction-markets-web 2>/dev/null)"
echo
echo "## unchanged touched files -- CR-stripped sha256(16), EXPECT box == my BASE (Stage-4/farm files untouched):"
for rel in prediction_markets/search_run.py scripts/pm_cli.py prediction_markets/web/templates/pm_farm_league.html; do
  f=$PKG/$rel
  if [ -f "$f" ]; then echo "  $rel = $(tr -d '\r' < "$f" | sha256sum | cut -c1-16)"; else echo "  $rel = MISSING"; fi
done
echo "  EXPECT base: search_run=311beb68fa68ef7b  pm_cli=7ae2f219a1b3d358  pm_farm_league=92af020168a88ed0"
echo
echo "## NEW file must be ABSENT on the box:"
nf=$PKG/prediction_markets/web/templates/partials/pm_search_status.html
[ -f "$nf" ] && echo "  pm_search_status.html = PRESENT (UNEXPECTED)" || echo "  pm_search_status.html = ABSENT (correct)"
echo
echo "## box app.py -- the GRAFT TARGET (EXPECT M4: is_admin count=10, /pm/arm count=0):"
A=$PKG/prediction_markets/web/app.py
echo "  is_admin count = $(grep -cE 'is_admin' "$A")   (M4=10, M5=12)"
echo "  /pm/arm count  = $(grep -cE '/pm/arm' "$A")    (M4=0, M5=1)"
echo "  app.py CR-stripped sha256(16) = $(tr -d '\r' < "$A" | sha256sum | cut -c1-16)"
echo "  imports os/subprocess/sys already present? os=$(grep -cE '^import os' "$A") subprocess=$(grep -cE '^import subprocess' "$A") sys=$(grep -cE '^import sys' "$A") pm_db_path=$(grep -cE 'pm_db_path' "$A")"
echo
echo "## schema head (EXPECT 19 -- no migration needed):"
$V - <<PY 2>/dev/null || echo "  (venv DB read failed)"
import sqlite3
c=sqlite3.connect("file:$DB?mode=ro", uri=True)
print("  schema head:", c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0], "(expect 19)")
print("  pm_search_run rows:", c.execute("SELECT COUNT(*) FROM pm_search_run").fetchone()[0])
print("  latest run:", c.execute("SELECT run_id,status,leaderboard_category,n_candidates_written FROM pm_search_run ORDER BY started_ts DESC LIMIT 1").fetchall())
PY
echo "### DONE ###"
