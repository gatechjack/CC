echo "=== MACE leg-sanity guard GRAFT (box-write; run only under Jack authorization) ==="
ROOT=/home/azureuser/trading_corp
STAGE=/tmp/mace_legsanity_stage
TS=$(date -u +%Y%m%dT%H%M%SZ)
BK="$HOME/mace_legsanity_graft_backup_$TS"
FILES="trading_corp/mace/strategy.py trading_corp/mace/execution.py trading_corp/mace/manager.py"
md5of() { md5sum "$1" | cut -c1-8; }
shaof() { sha256sum "$1" | cut -c1-12; }

echo "--- 1) DRIFT-GATE: box CURRENT must == base (live 9/19 guard deploy) ---"
FAIL=0
for pair in \
  "trading_corp/mace/config.py:01117cca" \
  "trading_corp/mace/strategy.py:69eef994" \
  "trading_corp/mace/execution.py:66dba55a" \
  "trading_corp/mace/manager.py:1d4334d7" ; do
  f="${pair%%:*}"; exp="${pair##*:}"; got=$(md5of "$ROOT/$f")
  [ "$got" = "$exp" ] && echo "  OK   $f md5=$got" || { echo "  DRIFT $f md5=$got expect=$exp"; FAIL=1; }
done
cfg_base=$(shaof "$ROOT/config/mace.yaml")
[ "$cfg_base" = "931a8214be50" ] && echo "  OK   config/mace.yaml sha=$cfg_base" || { echo "  DRIFT mace.yaml sha=$cfg_base expect=931a8214be50"; FAIL=1; }
if [ "$FAIL" != 0 ]; then echo "ABORT: box drifted from base; do NOT graft. Reconcile first."; exit 3; fi

echo "--- 2) BACKUP the 3 target files -> $BK ---"
mkdir -p "$BK/trading_corp/mace"
for f in $FILES; do cp -p "$ROOT/$f" "$BK/$f"; done
ls -R "$BK"

echo "--- 3) STAGE + normalize (tr -d CR) + cp into live tree (3 files) ---"
rm -rf "$STAGE"; mkdir -p "$STAGE"
tar xzf /tmp/mace_legsanity_graft.tar.gz -C "$STAGE" || { echo "ABORT: tar extract failed"; exit 3; }
for f in $FILES; do tr -d '\r' < "$STAGE/$f" > "$ROOT/$f" || { echo "ABORT: cp $f failed"; FAIL=1; break; }; done

echo "--- 4) RE-VERIFY box == NEW target (3 changed) + config.py/mace.yaml UNTOUCHED ---"
for pair in \
  "trading_corp/mace/strategy.py:50dd6984" \
  "trading_corp/mace/execution.py:5b2307bc" \
  "trading_corp/mace/manager.py:b0e9e879" ; do
  f="${pair%%:*}"; exp="${pair##*:}"; got=$(md5of "$ROOT/$f")
  [ "$got" = "$exp" ] && echo "  OK   $f md5=$got" || { echo "  MISMATCH $f md5=$got expect=$exp"; FAIL=1; }
done
cfg_unch=$(md5of "$ROOT/trading_corp/mace/config.py")
[ "$cfg_unch" = "01117cca" ] && echo "  OK   config.py UNTOUCHED md5=$cfg_unch" || { echo "  MISMATCH config.py md5=$cfg_unch (expect untouched 01117cca)"; FAIL=1; }
yaml_unch=$(shaof "$ROOT/config/mace.yaml")
[ "$yaml_unch" = "931a8214be50" ] && echo "  OK   config_hash UNCHANGED sha=$yaml_unch" || { echo "  MISMATCH mace.yaml sha=$yaml_unch"; FAIL=1; }

echo "--- 5) py_compile the 3 grafted + config.py (import closure) ---"
PY=$(systemctl show -p ExecStart --value trading-corp 2>/dev/null | grep -oE '/[^ ;]*python[0-9.]*' | head -1)
[ -x "$PY" ] || PY="$ROOT/venv/bin/python"
"$PY" -m py_compile "$ROOT/trading_corp/mace/strategy.py" "$ROOT/trading_corp/mace/execution.py" "$ROOT/trading_corp/mace/manager.py" "$ROOT/trading_corp/mace/config.py" && echo "  py_compile OK" || { echo "  py_compile FAILED"; FAIL=1; }

if [ "$FAIL" != 0 ]; then
  echo "*** GRAFT FAILED -> ROLLING BACK from $BK ***"
  for f in $FILES; do cp -p "$BK/$f" "$ROOT/$f"; done
  echo "rolled back. box restored to base. NO restart. Investigate."
  exit 4
fi
rm -rf "$STAGE" /tmp/mace_legsanity_graft.tar.gz
echo "=== GRAFT OK - 3 files applied; config_hash 931a8214be50 UNCHANGED. Backup: $BK ==="
echo "=== NEXT (Jack): restart_tc.ps1, then mace_legsanity_bootverify_ro.ps1 ==="
