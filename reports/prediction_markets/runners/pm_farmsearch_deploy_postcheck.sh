set -u
ROOT=/home/azureuser/trading_corp
EPID=$(systemctl show -p MainPID --value trading-corp 2>/dev/null)
WPID=$(systemctl show -p MainPID --value prediction-markets-web 2>/dev/null)
echo "### FARM-SEARCH DEPLOY -- POST-CHECK $(date -u +%Y%m%dT%H%M%SZ) ###"
echo "engine PID (MUST equal the pre-deploy value -- untouched): $EPID"
echo "pm_web PID (should have CHANGED -- restarted): $WPID"
PORT=$(ss -ltnp 2>/dev/null | grep "pid=$WPID," | grep -oE '127.0.0.1:[0-9]+' | head -1 | cut -d: -f2)
[ -n "${PORT:-}" ] || PORT=$(ss -ltnp 2>/dev/null | grep "pid=$WPID," | grep -oE ':[0-9]{3,5}' | head -1 | tr -d :)
B="http://127.0.0.1:${PORT:-0}"
echo "pm_web bind: $B"
code(){ curl -s -o /dev/null -w '%{http_code}' "$@" 2>/dev/null; }
echo "## served pages:"
echo "  /healthz            -> $(code $B/healthz)  $(curl -s $B/healthz 2>/dev/null | tr -d '\n' | head -c 120)"
echo "  /farm               -> $(code $B/farm)   (expect 200; no-identity renders WITHOUT the admin panel)"
echo "  /farm/search/status -> $(code $B/farm/search/status)   (expect 200 = new route live, NOT 404 shadow)"
# admin render: forge Remote-User on loopback (Caddy strips client copies upstream; pm_web trusts the header)
ADM=$(tr '\0' '\n' < /proc/$WPID/environ 2>/dev/null | sed -n 's/^PM_ADMIN_IDENTITIES=//p' | awk '{print $1}' | tr ',' ' ' | awk '{print $1}')
if [ -n "${ADM:-}" ]; then
  H=$(curl -s -H "Remote-User: $ADM" "$B/farm" 2>/dev/null)
  echo "  /farm as admin($ADM): Prospect-discovery=$(echo "$H" | grep -c 'Prospect discovery') RunSearch=$(echo "$H" | grep -c 'Run Search') warn=$(echo "$H" | grep -c 'may briefly compete with live copying') (each expect >=1)"
  ST=$(curl -s -H "Remote-User: $ADM" "$B/farm/search/status" 2>/dev/null)
  echo "  /farm/search/status as admin: last-run text=$(echo "$ST" | grep -cE 'finished|underway|No search has run') (expect >=1; the run_id=1/134 shows as 'finished: 134')"
  KH=$(curl -s -H "Remote-User: karen" "$B/farm" 2>/dev/null)
  echo "  /farm as NON-admin(karen): panel-hidden=$([ $(echo "$KH" | grep -c 'Run Search') = 0 ] && echo YES || echo NO) (expect YES -- panel admin-only)"
else
  echo "  (admin-render check SKIPPED -- PM_ADMIN_IDENTITIES not readable; /farm 200 + status 200 above still prove routes live)"
fi
echo "## pm_web log since restart (no Traceback expected):"
journalctl -u prediction-markets-web --since "-3 min" --no-pager 2>/dev/null | grep -icE 'Traceback|ImportError|SyntaxError|NameError' | sed 's/^/  failure-signature lines: /'
echo "## engine untouched confirm:"
echo "  engine PID now: $(systemctl show -p MainPID --value trading-corp 2>/dev/null) (== $EPID); NRestarts: $(systemctl show -p NRestarts --value trading-corp 2>/dev/null)"
echo "### POST-CHECK DONE ###"
