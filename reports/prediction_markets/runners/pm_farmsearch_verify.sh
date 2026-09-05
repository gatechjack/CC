set -u
ROOT=/home/azureuser/trading_corp
PKG=$ROOT/trading_corp
PART=prediction_markets/web/templates/partials/pm_search_status.html
A=$PKG/prediction_markets/web/app.py
echo "### FARM-SEARCH DEPLOY -- STATE VERIFY (READ-ONLY; nothing written/restarted) $(date -u +%Y%m%dT%H%M%SZ) ###"
echo
echo "## [1] deployed file hashes (CR-stripped sha256(16)) vs TARGETS:"
vc(){ h=$(tr -d '\r' < "$PKG/$1" 2>/dev/null | sha256sum | cut -c1-16); echo "  $1 = $h  [$([ "$h" = "$2" ] && echo MATCH || echo MISMATCH-expected-$2)]"; }
vc prediction_markets/search_run.py a15acc3a7c28fc30
vc scripts/pm_cli.py b5cb0b91fa84683a
vc prediction_markets/web/templates/pm_farm_league.html 3ccf80ddc42cae40
vc "$PART" 59b287dc51b24529
vc prediction_markets/web/app.py 34bb61ed92b7cd1b
echo
echo "## [2] app.py GRAFT CLEAN (MUST be is_admin=14, /pm/arm=0 -- proven by count, no M5 leak):"
echo "  is_admin = $(grep -cE 'is_admin' "$A")   /pm/arm = $(grep -cE '/pm/arm' "$A")"
echo
echo "## [3] apply evidence -- backup dir(s) + stage:"
ls -dt /home/azureuser/pm_farmsearch_deploy_backup_* 2>/dev/null | head -3 | while read d; do echo "  backup: $d  ($(stat -c '%y' "$d" 2>/dev/null | cut -d. -f1))"; done
[ -d /home/azureuser/pm_farmsearch_stage ] && echo "  stage dir: PRESENT (apply may not have completed cleanup)" || echo "  stage dir: removed (apply completed)"
echo
echo "## [4] pm_web restart timing + engine untouched:"
WPID=$(systemctl show -p MainPID --value prediction-markets-web 2>/dev/null); EPID=$(systemctl show -p MainPID --value trading-corp 2>/dev/null)
echo "  pm_web : PID=$WPID  start=$(systemctl show -p ActiveEnterTimestamp --value prediction-markets-web 2>/dev/null)  NRestarts=$(systemctl show -p NRestarts --value prediction-markets-web 2>/dev/null)  (was 191017 pre-deploy)"
echo "  engine : PID=$EPID  start=$(systemctl show -p ActiveEnterTimestamp --value trading-corp 2>/dev/null)  NRestarts=$(systemctl show -p NRestarts --value trading-corp 2>/dev/null)  (MUST be 196060 -- untouched)"
echo
PORT=$(ss -ltnp 2>/dev/null | grep "pid=$WPID," | grep -oE ':[0-9]{3,5}' | head -1 | tr -d :)
B="http://127.0.0.1:${PORT:-0}"
echo "## [5] served pages ($B):"
echo "  /healthz            -> $(curl -s -o /dev/null -w '%{http_code}' $B/healthz 2>/dev/null)  $(curl -s $B/healthz 2>/dev/null | tr -d '\n' | head -c 120)"
echo "  /farm               -> $(curl -s -o /dev/null -w '%{http_code}' $B/farm 2>/dev/null)"
echo "  /farm/search/status -> $(curl -s -o /dev/null -w '%{http_code}' $B/farm/search/status 2>/dev/null)  (expect 200, NOT 404)"
ADM=$(tr '\0' '\n' < /proc/$WPID/environ 2>/dev/null | sed -n 's/^PM_ADMIN_IDENTITIES=//p' | awk '{print $1}' | tr ',' ' ' | awk '{print $1}')
if [ -n "${ADM:-}" ]; then
  H=$(curl -s -H "Remote-User: $ADM" $B/farm 2>/dev/null)
  echo "  /farm as admin($ADM): Prospect-discovery=$(echo "$H" | grep -c 'Prospect discovery') RunSearch=$(echo "$H" | grep -c 'Run Search') warn=$(echo "$H" | grep -c 'may briefly compete with live copying')  (each >=1)"
  echo "  /farm/search/status as admin: $(curl -s -H "Remote-User: $ADM" $B/farm/search/status 2>/dev/null | grep -oE 'finished|underway|No search has run' | head -1)  (run_id=1/134 -> 'finished')"
  echo "  /farm as NON-admin(karen): RunSearch count = $(curl -s -H 'Remote-User: karen' $B/farm 2>/dev/null | grep -c 'Run Search')  (expect 0 -- panel admin-only)"
else
  echo "  (admin-render check skipped -- PM_ADMIN_IDENTITIES not readable; the 200s above prove the routes are live)"
fi
echo
echo "## [6] pm_web journal failure-signatures (last 6 min):"
journalctl -u prediction-markets-web --since '-6 min' --no-pager 2>/dev/null | grep -icE 'Traceback|ImportError|SyntaxError|NameError|AttributeError' | sed 's/^/  matches: /'
echo
echo "## [7] PM DB unchanged by the deploy (schema + no new lock rows from the deploy itself):"
$ROOT/venv/bin/python - <<PY 2>/dev/null
import sqlite3
c=sqlite3.connect("file:$ROOT/data/prediction_markets.db?mode=ro", uri=True)
print("  schema head:", c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0], "(expect 19)")
print("  pm_search_run rows:", c.execute("SELECT COUNT(*) FROM pm_search_run").fetchone()[0], "(expect 1 -- the deploy writes NO row)")
print("  rows:", c.execute("SELECT run_id,status,leaderboard_category,n_candidates_written FROM pm_search_run ORDER BY run_id").fetchall())
PY
echo "### VERIFY DONE ###"
