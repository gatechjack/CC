set -e
# RESERVED box write -- grafts the 3 entry-ladder-orphan-fix source files. Drift-gated (box==BASE),
# backup-first, CR-stripped apply, verify==TARGET, py_compile, rollback-on-any-failure. Code-only:
# config/mace.yaml untouched -> config_hash MUST stay 931a8214be50. RESTART REQUIRED after (restart_tc.ps1).
STAGE=/tmp/mace_pf_src.tgz
ROOT=/home/azureuser/trading_corp
TS=$(date -u +%Y%m%dT%H%M%SZ)
BK="/home/azureuser/mace_pf_graft_backup_$TS"
FILES="trading_corp/brokers/robinhood.py trading_corp/mace/rh_broker.py trading_corp/mace/execution.py"
base_of(){ case "$1" in
  trading_corp/brokers/robinhood.py) echo 2753939c;;
  trading_corp/mace/rh_broker.py)    echo c8aa5789;;
  trading_corp/mace/execution.py)    echo 71f1a828;; esac; }
want_of(){ case "$1" in
  trading_corp/brokers/robinhood.py) echo 1e6bbc1e;;
  trading_corp/mace/rh_broker.py)    echo bac4411a;;
  trading_corp/mace/execution.py)    echo e380c2f6;; esac; }
rm -rf /tmp/mace_pf_stage_x; mkdir -p /tmp/mace_pf_stage_x; tar xzf "$STAGE" -C /tmp/mace_pf_stage_x
echo "=== 1) DRIFT GATE: box CURRENT == BASE (abort on any drift) ==="
for f in $FILES; do got=$(tr -d '\r' < "$ROOT/$f" | md5sum | cut -c1-8)
  if [ "$got" != "$(base_of "$f")" ]; then echo "DRIFT ABORT $f box=$got base=$(base_of "$f")"; exit 2; fi
  echo "base-ok $f $got"; done
echo "=== 2) STAGED == TARGET ==="
for f in $FILES; do got=$(tr -d '\r' < "/tmp/mace_pf_stage_x/$f" | md5sum | cut -c1-8)
  if [ "$got" != "$(want_of "$f")" ]; then echo "STAGE BAD $f staged=$got want=$(want_of "$f")"; exit 3; fi; done
echo "=== 3) backup ==="
for f in $FILES; do mkdir -p "$BK/$(dirname "$f")"; cp "$ROOT/$f" "$BK/$f"; done
echo "backup=$BK"
rollback(){ for f in $FILES; do cp "$BK/$f" "$ROOT/$f"; done; echo "ROLLED BACK from $BK"; }
echo "=== 4) apply (CR-stripped, atomic per file) ==="
for f in $FILES; do tr -d '\r' < "/tmp/mace_pf_stage_x/$f" > "$ROOT/$f.pfnew" && mv "$ROOT/$f.pfnew" "$ROOT/$f"; done
echo "=== 5) verify POST == TARGET ==="
for f in $FILES; do got=$(tr -d '\r' < "$ROOT/$f" | md5sum | cut -c1-8)
  if [ "$got" != "$(want_of "$f")" ]; then echo "POST MISMATCH $f got=$got"; rollback; exit 4; fi
  echo "target-ok $f $got"; done
echo "=== 6) py_compile ==="
if ! "$ROOT/venv/bin/python" -m py_compile "$ROOT/trading_corp/brokers/robinhood.py" "$ROOT/trading_corp/mace/rh_broker.py" "$ROOT/trading_corp/mace/execution.py"; then echo "py_compile FAIL"; rollback; exit 5; fi
echo "py_compile OK"
echo "config_hash (expect UNCHANGED 931a8214be50): $(cd "$ROOT" && python3 -c "import hashlib;print(hashlib.sha256(open('config/mace.yaml','rb').read()).hexdigest()[:12])")"
echo "GRAFT OK -- 3 files applied. RESTART REQUIRED: restart_tc.ps1 (bounces all divisions ~3.5min). backup=$BK"
