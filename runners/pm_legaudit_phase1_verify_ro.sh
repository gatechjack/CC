set -u
TS=$(date -u +%Y%m%dT%H%M%SZ)
ROOT=/home/azureuser/trading_corp
GRAFT_EPOCH=$(date -d "2026-09-17 19:28:27 UTC" +%s)   # phase-1 graft time; pm_web ActiveEnter MUST postdate this
m(){ md5sum "$1" | cut -d' ' -f1; }
echo "### PHASE 1 VERIFY (pm_web restart + served strip; READ-ONLY) $TS ###"
EPID=$(systemctl show -p MainPID --value trading-corp 2>/dev/null)
ENR=$(systemctl show -p NRestarts --value trading-corp 2>/dev/null)
WPID=$(systemctl show -p MainPID --value prediction-markets-web 2>/dev/null)
WSTATE=$(systemctl is-active prediction-markets-web 2>/dev/null)
WSINCE=$(systemctl show -p ActiveEnterTimestamp --value prediction-markets-web 2>/dev/null)
WSINCE_EPOCH=$(date -d "$WSINCE" +%s 2>/dev/null)
echo "  engine trading-corp    : PID=$EPID (expect UNCHANGED 458566) NRestarts=$ENR"
echo "  pm_web (pred-markets-web): PID=$WPID (expect CHANGED from 436431) state=$WSTATE"
echo "  pm_web ActiveEnterTimestamp: $WSINCE"
echo "     graft_epoch=$GRAFT_EPOCH  pm_web_active_epoch=${WSINCE_EPOCH:-PARSE_FAIL}"
if [ -n "${WSINCE_EPOCH:-}" ] && [ "$WSINCE_EPOCH" -gt "$GRAFT_EPOCH" ]; then
  echo "     -> RESTART CONFIRMED: pm_web ActiveEnter POSTDATES the graft (not just exit code)"
else
  echo "     -> ** RESTART NOT CONFIRMED: ActiveEnter does NOT postdate the graft -- pm_web may not have restarted"
fi
echo "  box leg_audit.py  md5 = $(m "$ROOT/trading_corp/prediction_markets/leg_audit.py")   (expect 61cfe84b094ab5b109452a18dc74f3bc = new/target)"
echo "  box live_driver.py md5 = $(m "$ROOT/trading_corp/prediction_markets/live_driver.py")  (expect b9e67d1a7117191fa64b3e31290ec0f8 = BASE; engine half NOT done)"
PORT=$(ss -ltnp 2>/dev/null | grep "pid=$WPID," | grep -oE '127.0.0.1:[0-9]+|0.0.0.0:[0-9]+|\*:[0-9]+' | grep -oE '[0-9]+$' | head -1)
[ -z "$PORT" ] && PORT=8081
BASE="http://127.0.0.1:$PORT"
echo "  pm_web base: $BASE"
echo
echo "### SERVED /live STRIP (read from the page per account identity) ###"
for u in jack karen; do
  f=/tmp/live_$u.html
  code=$(curl -s -H "Remote-User:$u" -o "$f" -w '%{http_code}' -m 60 "$BASE/live" 2>/dev/null)
  cnt=$(grep -oE 'data-legaudit-count="[0-9]+"' "$f" 2>/dev/null | grep -oE '[0-9]+' | head -1)
  hdr=$(grep -oE '[0-9]+ LEG AUDIT[S]? TO REVIEW' "$f" 2>/dev/null | head -1)
  soft=$(grep -cF '~ code review' "$f" 2>/dev/null)
  uneval=$(grep -cF '? could-not-check' "$f" 2>/dev/null)
  inv=$(grep -cF '*** INVERSION' "$f" 2>/dev/null)
  lg=$(grep -cF 'KXCS2GAME-26SEP171100NIPLG-LG' "$f" 2>/dev/null)
  alias=$(grep -cF 'ok:code_alias' "$f" 2>/dev/null)
  echo "  [$u] GET /live -> HTTP $code ; strip total=${cnt:-<no strip / 0>} ; header='${hdr:-none}'"
  echo "       rows: '~ code review'=$soft  '? could-not-check'=$uneval  '*** INVERSION'=$inv  | LG-ticker rows=$lg  ok:code_alias-in-page=$alias"
  rm -f "$f"
done
echo
echo "### VERDICT (Phase-1 PASS = ALL of): pm_web restarted(postdates graft) + engine 458566 unchanged + leg_audit.py=target + live_driver.py=base + LG rows show 'code review' + ZERO could-not-check + ZERO inversion ###"
echo "### DONE ###"
