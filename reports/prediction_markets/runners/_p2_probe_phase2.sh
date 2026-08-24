#!/usr/bin/env bash
# PM P2 CP2-Phase2 BOX-SCRATCH: prove the scoreboard test suite GREEN in isolation. READ-ONLY to prod
# (reads systemctl for the engine-PID bracket only). Writes ONLY ~/pm_p2_scratch, ~/pm_p2_stage.tgz,
# /tmp/pm_p2_test_*.db -- all deleted at end. Never touches the prod PM DB / engine / legacy DB.
# LOCAL refs to compare: TARBALL sha256 f640c2bf762e64ff9a46823e329b106abd0bded5ff0e936ca5ef489579d0b8db
#   per-file(12): stats e8cd1f979094 app 2b07add5342c base 7e69610cbf90 macros f8b74472875e board 2e806a512fe9
#                 partial bb332eb7c070 css aa2dbfa449f8 htmx 491955cd1810 test b37c0c48818e
echo "=== PM P2 CP2-Ph2 BOX-SCRATCH (start) ==="; date -u
H="${HOME:-/home/azureuser}"; ROOT="$H/trading_corp"; VP="$ROOT/venv/bin/python"
S="$H/pm_p2_scratch"; STAGE="$H/pm_p2_stage.tgz"; TDB="/tmp/pm_p2_test_$$.db"
echo "whoami=$(whoami) H=$H VP=$VP"

echo ""; echo "=== [0] ENGINE BEFORE ==="
systemctl show -p MainPID -p ActiveState -p SubState trading-corp.service 2>&1
PID0=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null); echo "PID0=$PID0"

echo ""; echo "=== [1] CHAIN OF CUSTODY: staged tarball (BOX sha256 must equal LOCAL f640c2bf...) ==="
ls -l "$STAGE" 2>&1; echo "TARBALL_SHA256_BOX:"; sha256sum "$STAGE" 2>&1

echo ""; echo "=== [2] SCRATCH SETUP (under \$H only; never prod) ==="
case "$S" in /home/*/pm_p2_scratch|/root/pm_p2_scratch) : ;; *) echo "REFUSE bad scratch path: $S"; exit 2 ;; esac
rm -rf "$S"; mkdir -p "$S"; tar -xzf "$STAGE" -C "$S" && echo "extracted OK"
echo "--- [2b] pyproject pytest cfg (asyncio_mode MUST be auto, else async tests ERROR) ---"
grep -nE 'asyncio_mode|filterwarnings|no:pytest' "$S/pyproject.toml" 2>&1
echo "--- [2c] Phase-2 files present + BOX sha256(12) (cf local refs in header) ---"
( cd "$S" && for f in trading_corp/prediction_markets/stats.py trading_corp/prediction_markets/web/app.py \
    trading_corp/prediction_markets/web/templates/pm_base.html trading_corp/prediction_markets/web/templates/pm_macros.html \
    trading_corp/prediction_markets/web/templates/pm_scoreboard.html trading_corp/prediction_markets/web/templates/partials/pm_scoreboard_table.html \
    trading_corp/prediction_markets/web/static/pm.css trading_corp/prediction_markets/web/static/htmx.min.js \
    tests/prediction_markets/test_scoreboard_render.py; do
    printf '  %s  %s\n' "$(sha256sum "$f" 2>/dev/null | cut -c1-12)" "$f"; done )

echo ""; echo "=== [3] PYTEST full PM suite (box venv, CWD=scratch, PM_DB_PATH=tmp FILE) ==="
export PM_DB_PATH="$TDB"; echo "PM_DB_PATH=$PM_DB_PATH"
( cd "$S" && PYTHONPATH="$S" "$VP" -m pytest tests/prediction_markets/ -p no:pytest_ethereum -q --junitxml="$S/junit.xml" 2>&1 )
RC=$?; echo "PYTEST_RC=$RC"
echo "--- [3b] junit testsuite line (tests/failures/errors) ---"; grep -oE '<testsuite [^>]*>' "$S/junit.xml" 2>&1 | head -1
echo "--- [3c] standalone-no-engine test (explicit -v; adding stats must NOT drag web/main/agents) ---"
( cd "$S" && PYTHONPATH="$S" "$VP" -m pytest tests/prediction_markets/test_web_healthz.py -p no:pytest_ethereum -k imports_no_engine -v 2>&1 | tail -6 )
echo "--- [3d] scoreboard render tests (explicit -v) ---"
( cd "$S" && PYTHONPATH="$S" "$VP" -m pytest tests/prediction_markets/test_scoreboard_render.py -p no:pytest_ethereum -v 2>&1 | tail -30 )

echo ""; echo "=== [4] ISOLATION: no *.db/-wal/-shm under scratch (expect NONE) ==="
find "$S" \( -name '*.db' -o -name '*.db-wal' -o -name '*.db-shm' \) 2>&1; echo "(end find)"

echo ""; echo "=== [5] CLEANUP + PROOF ==="
cd "$H"; rm -rf "$S"; rm -f "$STAGE"; rm -f "$TDB"*
if [ -e "$S" ]; then echo "SCRATCH_STILL_THERE=BAD"; else echo "SCRATCH_CONFIRMED_GONE"; fi
if [ -e "$STAGE" ]; then echo "STAGE_STILL_THERE=BAD"; else echo "STAGE_CONFIRMED_GONE"; fi

echo ""; echo "=== [6] ENGINE AFTER (unchanged proof) ==="
PID1=$(systemctl show -p MainPID --value trading-corp.service 2>/dev/null); echo "PID1=$PID1 PID0=$PID0"
if [ "$PID0" = "$PID1" ] && [ -n "$PID0" ]; then echo "ENGINE_PID_UNCHANGED=GOOD"; else echo "ENGINE_PID_CHANGED_OR_UNKNOWN=INVESTIGATE"; fi
echo "=== PM P2 CP2-Ph2 BOX-SCRATCH (done) ==="
