ROOT=/home/azureuser/trading_corp
PY=$ROOT/venv/bin/python
STAGE=/tmp/pmc_bandC_stage
TS=$(date -u +%Y%m%dT%H%M%SZ)
BK=/home/azureuser/pm_bandC_backup_$TS
csha(){ tr -d '\r' < "$1" | sha256sum | cut -c1-16; }
REL=(trading_corp/prediction_markets/web/marks.py trading_corp/prediction_markets/web/poller.py trading_corp/prediction_markets/web/app.py)
BASE=(8cace4e71d8140a0 e5016c5de162f1a3 911d4d17869bd68d)
TGT=(4d66cf66ee7fef14 5115b00264a7578f e49e337148b41bbc)
echo "########## BAND C GRAFT (mark-poller by-held-ticker) ##########"
echo "utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
rm -rf "$STAGE"; mkdir -p "$STAGE"
tar -xzf /tmp/pmc_bandC_deploy.tar.gz -C "$STAGE" || { echo "FATAL: extract"; exit 2; }
FAIL=0
for i in 0 1 2; do
  boxsha=$(csha "$ROOT/${REL[$i]}"); stgsha=$(csha "$STAGE/${REL[$i]}")
  echo "[${REL[$i]}] box=$boxsha base=${BASE[$i]} stage=$stgsha target=${TGT[$i]}"
  [ "$boxsha" != "${BASE[$i]}" ] && { echo "  DRIFT: box != BASE (box not at d68d402e) -- ABORT"; FAIL=1; }
  [ "$stgsha" != "${TGT[$i]}" ] && { echo "  STAGE != TARGET -- ABORT"; FAIL=1; }
done
if [ "$FAIL" != "0" ]; then echo "PRECHECK FAILED -- box UNTOUCHED"; rm -rf "$STAGE" /tmp/pmc_bandC_deploy.tar.gz; exit 3; fi
echo "precheck OK: all 3 box==BASE and stage==TARGET"
mkdir -p "$BK"
for i in 0 1 2; do cp "$ROOT/${REL[$i]}" "$BK/$(basename ${REL[$i]})"; cp "$STAGE/${REL[$i]}" "$ROOT/${REL[$i]}"; done
echo "applied 3 files; backup=$BK"
VFAIL=0
for i in 0 1 2; do live=$(csha "$ROOT/${REL[$i]}"); [ "$live" != "${TGT[$i]}" ] && { echo "  POST MISMATCH ${REL[$i]} live=$live != ${TGT[$i]}"; VFAIL=1; }; done
"$PY" -m py_compile "$ROOT/${REL[0]}" "$ROOT/${REL[1]}" "$ROOT/${REL[2]}" || { echo "  py_compile FAILED"; VFAIL=1; }
if [ "$VFAIL" != "0" ]; then
  echo "POST-VERIFY FAILED -- ROLLING BACK ALL"
  for i in 0 1 2; do cp "$BK/$(basename ${REL[$i]})" "$ROOT/${REL[$i]}"; done
  echo "rolled back from $BK"; rm -rf "$STAGE" /tmp/pmc_bandC_deploy.tar.gz; exit 4
fi
echo "POST-VERIFY OK: 3 files == TARGET + py_compile clean. backup=$BK"
rm -rf "$STAGE" /tmp/pmc_bandC_deploy.tar.gz
echo "########## GRAFT COMPLETE -- pm_web NOT restarted (Board: restart + FF next) ##########"
