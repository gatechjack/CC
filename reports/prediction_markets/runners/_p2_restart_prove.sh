#!/usr/bin/env bash
# PM P2 CP2-Ph2 RESTART + PROVE (root, via az run-command). Restarts ONLY pm_web (never trading-corp), then
# proves /healthz + /scoreboard render the LIVE anchors (BetMechanic nba one-sided 1132/roi~0.33 [per-category];
# Kickstand7 two-sided [per (wallet,category)]) and that the engine PID is unchanged. All DB reads are mode=ro
# (root must NOT open the WAL DB rw => no root-owned -wal/-shm, GOTCHA-1). Changes only the pm_web service state.
set -u
echo "=== PM P2 CP2-Ph2 RESTART+PROVE (start) ==="; date -u; echo "whoami=$(whoami)"
DB="/home/azureuser/trading_corp/data/prediction_markets.db"
VP="/home/azureuser/trading_corp/venv/bin/python"
BASE="http://127.0.0.1:8081"

echo "--- [0] engine PID BEFORE (trading-corp.service) ---"
PID0=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null); echo "PID0=$PID0"

echo "--- [1] restart pm_web ONLY ---"
systemctl restart prediction-markets-web.service
systemctl show -p MainPID -p ActiveState -p SubState prediction-markets-web.service 2>&1

echo "--- [2] /healthz (retry up to ~8s while the app binds; expect 200 + schema 4) ---"
HZ=000
for i in 1 2 3 4 5 6 7 8; do sleep 1; HZ=$(curl -s -o /tmp/pm_hz.json -w "%{http_code}" "$BASE/healthz" 2>/dev/null); [ "$HZ" = "200" ] && break; done
echo "healthz HTTP $HZ (after ${i}s)"; cat /tmp/pm_hz.json 2>&1; echo

echo "--- [3] DB read-only anchors (mode=ro) -> emit BM_ROI BM_N KS_CAT KS_PCT ---"
ANCH=$("$VP" - <<'PY' 2>/tmp/pm_anch.err
import sqlite3
c=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True);q=c.cursor()
bm=q.execute("SELECT roi,n_resolved FROM pm_category_onesided_stats WHERE wallet LIKE '0xa6a856a8c8a7%' AND category='nba'").fetchone()
ks=q.execute("SELECT category,two_sided_pct,n_condition_ids,n_two_sided FROM pm_category_stats WHERE wallet LIKE '0xd1acd3925d89%' AND n_condition_ids>0 ORDER BY two_sided_pct DESC LIMIT 1").fetchone()
import sys
bm_roi=('%+.1f'%(bm[0]*100)) if bm and bm[0] is not None else 'NA'
bm_n=str(bm[1]) if bm else 'NA'
ks_cat=ks[0] if ks else 'NA'
ks_pct=('%.0f'%(ks[1]*100)) if ks and ks[1] is not None else 'NA'
print('%s %s %s %s'%(bm_roi,bm_n,ks_cat,ks_pct))
print('DB_RAW BetMechanic(nba) onesided=%r  Kickstand7 top per-cat two-sided=%r'%(bm,ks),file=sys.stderr)
c.close()
PY
)
cat /tmp/pm_anch.err 2>&1
echo "ANCHORS_STDOUT: $ANCH"
read BM_ROI BM_N KS_CAT KS_PCT <<< "$ANCH"
echo "parsed: BM_ROI=$BM_ROI BM_N=$BM_N KS_CAT=$KS_CAT KS_PCT=$KS_PCT"

echo "--- [4] BetMechanic nba (per-category anchor): /scoreboard?category=nba ---"
curl -s "$BASE/scoreboard?category=nba&min_resolved=1" -o /tmp/pm_nba.html -w "  nba page HTTP %{http_code}\n" 2>&1
echo "  BetMechanic wallet present : $(grep -oc '0xa6a856a8c8a7' /tmp/pm_nba.html 2>/dev/null || echo 0)"
echo "  one-sided n=$BM_N present   : $(grep -c "$BM_N" /tmp/pm_nba.html 2>/dev/null || echo 0)"
echo "  one-sided roi $BM_ROI shown : $(grep -cF -- "$BM_ROI" /tmp/pm_nba.html 2>/dev/null || echo 0)"
echo "  upper-bound tag present     : $(grep -c 'bound' /tmp/pm_nba.html 2>/dev/null || echo 0)"

echo "--- [5] Kickstand7 two-sided (per (wallet,category) grain): /scoreboard?category=$KS_CAT ---"
curl -s "$BASE/scoreboard?category=$KS_CAT&min_resolved=1" -o /tmp/pm_ks.html -w "  ks page HTTP %{http_code}\n" 2>&1
echo "  Kickstand7 wallet present  : $(grep -oc '0xd1acd3925d89' /tmp/pm_ks.html 2>/dev/null || echo 0)"
echo "  two-sided ${KS_PCT}% shown  : $(grep -c "${KS_PCT}%" /tmp/pm_ks.html 2>/dev/null || echo 0)"
echo "  grain label present        : $(grep -c 'per (wallet, category)' /tmp/pm_ks.html 2>/dev/null || echo 0)"

echo "--- [6] structural checks on the nba page ---"
echo "  refresh band present : $(grep -c 'data-refresh-band=' /tmp/pm_nba.html 2>/dev/null || echo 0)"
echo "  scoreboard 200 (all) : $(curl -s -o /dev/null -w '%{http_code}' "$BASE/scoreboard?min_resolved=1" 2>&1)"

echo "--- [7] engine PID AFTER (must equal BEFORE; pm_web restart must not touch trading-corp) ---"
PID1=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null); echo "PID1=$PID1 PID0=$PID0"
if [ "$PID0" = "$PID1" ] && [ -n "$PID0" ]; then echo "ENGINE_PID_UNCHANGED=GOOD"; else echo "ENGINE_PID_CHANGED=INVESTIGATE"; fi

echo "--- [8] cleanup tmp (no residue) ---"
rm -f /tmp/pm_hz.json /tmp/pm_anch.err /tmp/pm_nba.html /tmp/pm_ks.html
echo "=== RESTART+PROVE (done) ==="
