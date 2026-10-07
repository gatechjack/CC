echo "=== MACE EXIT-REDESIGN GRAFT (5 files; box-write; run ONLY under Jack authorization) ==="
ROOT=/home/azureuser/trading_corp
STAGE=/tmp/mace_exitredesign_stage
TS=$(date -u +%Y%m%dT%H%M%SZ)
BK="$HOME/mace_exitredesign_graft_backup_$TS"
FILES="trading_corp/mace/domain.py trading_corp/mace/execution.py trading_corp/mace/strategy.py trading_corp/mace/manager.py trading_corp/mace/config.py"
md5of() { tr -d '\r' < "$1" | md5sum | cut -c1-8; }
shaof() { sha256sum "$1" | cut -c1-12; }
FAIL=0

echo "--- 1) DRIFT-GATE: box CURRENT must == box-truth base (2026-10-06 deployed bytes) ---"
for pair in \
  "trading_corp/mace/domain.py:877a2c72" \
  "trading_corp/mace/execution.py:5b2307bc" \
  "trading_corp/mace/strategy.py:50dd6984" \
  "trading_corp/mace/manager.py:31357a78" \
  "trading_corp/mace/config.py:01117cca" ; do
  f="${pair%%:*}"; exp="${pair##*:}"; got=$(md5of "$ROOT/$f")
  [ "$got" = "$exp" ] && echo "  OK   $f md5=$got" || { echo "  DRIFT $f md5=$got expect=$exp"; FAIL=1; }
done
cfg_base=$(shaof "$ROOT/config/mace.yaml")
[ "$cfg_base" = "931a8214be50" ] && echo "  OK   config/mace.yaml sha=$cfg_base" || { echo "  DRIFT mace.yaml sha=$cfg_base expect=931a8214be50"; FAIL=1; }
if [ "$FAIL" != 0 ]; then echo "ABORT: box drifted from base; do NOT graft. Reconcile first."; exit 3; fi

echo "--- 2) EXTRACT + PRE-APPLY STAGED-HASH GATE: staged bytes must == NEW target BEFORE any write ---"
rm -rf "$STAGE"; mkdir -p "$STAGE"
tar xzf /tmp/mace_exitredesign_graft.tar.gz -C "$STAGE" || { echo "ABORT: tar extract failed"; exit 3; }
for pair in \
  "trading_corp/mace/domain.py:8522dc3a" \
  "trading_corp/mace/execution.py:9cc1c165" \
  "trading_corp/mace/strategy.py:b147e59d" \
  "trading_corp/mace/manager.py:ccd02002" \
  "trading_corp/mace/config.py:d441957f" ; do
  f="${pair%%:*}"; exp="${pair##*:}"; got=$(md5of "$STAGE/$f")
  [ "$got" = "$exp" ] && echo "  OK   staged $f md5=$got" || { echo "  STAGE-MISMATCH $f md5=$got expect=$exp"; FAIL=1; }
done
if [ "$FAIL" != 0 ]; then echo "ABORT: staged bytes != target; box UNTOUCHED."; rm -rf "$STAGE"; exit 3; fi

echo "--- 3) BACKUP the 5 target files -> $BK ---"
mkdir -p "$BK/trading_corp/mace"
for f in $FILES; do cp -p "$ROOT/$f" "$BK/$f"; done
ls -R "$BK"

echo "--- 4) APPLY (tr -d CR) into the live tree (5 files) ---"
for f in $FILES; do tr -d '\r' < "$STAGE/$f" > "$ROOT/$f" || { echo "ABORT: cp $f failed"; FAIL=1; break; }; done

echo "--- 5) RE-VERIFY box == NEW target (5 changed) + config/mace.yaml data UNCHANGED ---"
for pair in \
  "trading_corp/mace/domain.py:8522dc3a" \
  "trading_corp/mace/execution.py:9cc1c165" \
  "trading_corp/mace/strategy.py:b147e59d" \
  "trading_corp/mace/manager.py:ccd02002" \
  "trading_corp/mace/config.py:d441957f" ; do
  f="${pair%%:*}"; exp="${pair##*:}"; got=$(md5of "$ROOT/$f")
  [ "$got" = "$exp" ] && echo "  OK   $f md5=$got" || { echo "  MISMATCH $f md5=$got expect=$exp"; FAIL=1; }
done
yaml_unch=$(shaof "$ROOT/config/mace.yaml")
[ "$yaml_unch" = "931a8214be50" ] && echo "  OK   config_hash UNCHANGED sha=$yaml_unch (code-only deploy)" || { echo "  MISMATCH mace.yaml sha=$yaml_unch"; FAIL=1; }

echo "--- 6) py_compile the 5 grafted files (import closure) ---"
PY=$(systemctl show -p ExecStart --value trading-corp 2>/dev/null | grep -oE '/[^ ;]*python[0-9.]*' | head -1)
[ -x "$PY" ] || PY="$ROOT/venv/bin/python"
"$PY" -m py_compile $(for f in $FILES; do echo "$ROOT/$f"; done) && echo "  py_compile OK" || { echo "  py_compile FAILED"; FAIL=1; }

if [ "$FAIL" != 0 ]; then
  echo "*** GRAFT FAILED -> ROLLING BACK from $BK ***"
  for f in $FILES; do cp -p "$BK/$f" "$ROOT/$f"; done
  echo "rolled back. box restored to base. NO restart. Investigate."
  exit 4
fi
rm -rf "$STAGE" /tmp/mace_exitredesign_graft.tar.gz
echo "=== GRAFT OK - 5 files applied; config_hash 931a8214be50 UNCHANGED. Backup: $BK ==="
echo "=== NEXT (Jack): restart_tc.ps1, then mace_exitredesign_bootverify_ro.ps1 ==="
