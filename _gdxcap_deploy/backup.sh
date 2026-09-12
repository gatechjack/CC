#!/bin/bash
# MACE #2 GDX-cap BACKUP + rollback authoring. POST-CLOSE, pre-graft.
set -u
ROOT=/home/azureuser/trading_corp; PKG=$ROOT/trading_corp
TS=$(date -u +%Y%m%dT%H%M%SZ)
BK=$ROOT/gdxcap_backup_$TS
RB=$ROOT/gdxcap_rollback_$TS.sh
mkdir -p "$BK" || { echo "backup mkdir failed"; exit 2; }
declare -A PA=( [maceyaml]=$ROOT/config/mace.yaml [config]=$PKG/mace/config.py [execution]=$PKG/mace/execution.py [manager]=$PKG/mace/manager.py )
echo "=== BACKUP -> $BK ==="
fail=0
for k in maceyaml config execution manager; do
  f=${PA[$k]}; b=$BK/$(basename "$f")
  cp -p "$f" "$b" || { echo "cp fail $k"; fail=1; continue; }
  m=$(md5sum < "$f" | cut -d' ' -f1); mb=$(md5sum < "$b" | cut -d' ' -f1)
  if [ "$m" = "$mb" ]; then echo "OK $k md5=$m"; else echo "MD5 MISMATCH $k"; fail=1; fi
done
[ $fail -eq 0 ] || { echo "*** BACKUP FAILED -- do not graft ***"; exit 3; }
cat > "$RB" <<EOF
#!/bin/bash
# Rollback the MACE #2 GDX-cap graft: restore the 4 files from $BK (== box-truth 7683f59b).
set -u
cp -p "$BK/mace.yaml" "$ROOT/config/mace.yaml"        && echo "restored config/mace.yaml"
cp -p "$BK/config.py" "$PKG/mace/config.py"           && echo "restored config.py"
cp -p "$BK/execution.py" "$PKG/mace/execution.py"     && echo "restored execution.py"
cp -p "$BK/manager.py" "$PKG/mace/manager.py"         && echo "restored manager.py"
echo "=== ROLLBACK DONE -- board must RESTART (restart_tc.ps1) to load the restored files. ==="
EOF
chmod +x "$RB"
echo "=== BACKUP GREEN ==="
echo "BACKUP_DIR=$BK"
echo "ROLLBACK  =$RB"
