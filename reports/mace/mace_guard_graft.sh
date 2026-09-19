echo "=== MACE mark-guard GRAFT (box-write; run only under Jack authorization) ==="
ROOT=/home/azureuser/trading_corp
STAGE=/tmp/mace_guard_stage
TS=$(date -u +%Y%m%dT%H%M%SZ)
BK="$HOME/mace_guard_graft_backup_$TS"
FILES="trading_corp/mace/config.py trading_corp/mace/strategy.py trading_corp/mace/execution.py trading_corp/mace/manager.py config/mace.yaml"

md5of() { md5sum "$1" | cut -c1-8; }
shaof() { sha256sum "$1" | cut -c1-12; }

echo "--- 1) DRIFT-GATE: box CURRENT files must == base 2362db46 ---"
FAIL=0
for pair in \
  "trading_corp/mace/config.py:30d9d99a" \
  "trading_corp/mace/strategy.py:5937a4e3" \
  "trading_corp/mace/execution.py:a65955bb" \
  "trading_corp/mace/manager.py:a83b62e1" ; do
  f="${pair%%:*}"; exp="${pair##*:}"; got=$(md5of "$ROOT/$f")
  [ "$got" = "$exp" ] && echo "  OK   $f md5=$got" || { echo "  DRIFT $f md5=$got expect=$exp"; FAIL=1; }
done
cfg_base=$(shaof "$ROOT/config/mace.yaml")
[ "$cfg_base" = "bfde856f1c46" ] && echo "  OK   config/mace.yaml sha=$cfg_base" || { echo "  DRIFT config/mace.yaml sha=$cfg_base expect=bfde856f1c46"; FAIL=1; }
if [ "$FAIL" != 0 ]; then echo "ABORT: box drifted from base 2362db46; do NOT graft. Reconcile first."; exit 3; fi

echo "--- 2) BACKUP live 5 files -> $BK ---"
mkdir -p "$BK/trading_corp/mace" "$BK/config"
for f in $FILES; do cp -p "$ROOT/$f" "$BK/$f"; done
ls -R "$BK" | head -20

echo "--- 3) STAGE + normalize (tr -d CR) + cp into the live tree ---"
rm -rf "$STAGE"; mkdir -p "$STAGE"
tar xzf /tmp/mace_guard_graft.tar.gz -C "$STAGE" || { echo "ABORT: tar extract failed"; exit 3; }
for f in $FILES; do tr -d '\r' < "$STAGE/$f" > "$ROOT/$f" || { echo "ABORT: cp $f failed"; FAIL=1; break; }; done

echo "--- 4) RE-VERIFY box == NEW target (LF md5 / sha) ---"
for pair in \
  "trading_corp/mace/config.py:01117cca" \
  "trading_corp/mace/strategy.py:69eef994" \
  "trading_corp/mace/execution.py:66dba55a" \
  "trading_corp/mace/manager.py:1d4334d7" ; do
  f="${pair%%:*}"; exp="${pair##*:}"; got=$(md5of "$ROOT/$f")
  [ "$got" = "$exp" ] && echo "  OK   $f md5=$got" || { echo "  MISMATCH $f md5=$got expect=$exp"; FAIL=1; }
done
cfg_new=$(shaof "$ROOT/config/mace.yaml")
[ "$cfg_new" = "931a8214be50" ] && echo "  OK   config/mace.yaml sha=$cfg_new (new config_hash)" || { echo "  MISMATCH config/mace.yaml sha=$cfg_new expect=931a8214be50"; FAIL=1; }

echo "--- 5) py_compile the 4 grafted modules ---"
PY=$(systemctl show -p ExecStart --value trading-corp 2>/dev/null | grep -oE '/[^ ;]*python[0-9.]*' | head -1)
[ -x "$PY" ] || PY="$ROOT/venv/bin/python"
"$PY" -m py_compile "$ROOT/trading_corp/mace/config.py" "$ROOT/trading_corp/mace/strategy.py" "$ROOT/trading_corp/mace/execution.py" "$ROOT/trading_corp/mace/manager.py" && echo "  py_compile OK" || { echo "  py_compile FAILED"; FAIL=1; }

if [ "$FAIL" != 0 ]; then
  echo "*** GRAFT FAILED -> ROLLING BACK from $BK ***"
  for f in $FILES; do cp -p "$BK/$f" "$ROOT/$f"; done
  echo "rolled back. box restored to base. NO restart. Investigate."
  exit 4
fi
rm -rf "$STAGE" /tmp/mace_guard_graft.tar.gz
echo "=== GRAFT OK - 5 files applied, config_hash 931a8214be50 staged. Backup: $BK ==="
echo "=== NEXT (Jack): restart_tc.ps1 (config read at boot), then mace_guard_bootverify_ro.ps1 ==="
