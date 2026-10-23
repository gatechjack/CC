# RESERVED box GRAFT -- applies the 5 durability-fix files to the LIVE tree (no restart; engine runs
# old code until restarted). scp+tar binary-exact (non-ASCII em-dashes). Drift-gate box==base ->
# backup -> apply -> verify box==target -> py_compile -> AUTO-ROLLBACK on any failure. Idempotent-safe:
# aborts untouched if the box already drifted off base.
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$wt = 'C:\Users\AA Incorporado\cc-mace-durability-fix-2026-10-23-wt'
$tgz = "$env:TEMP\mace_durfix_graft.tgz"
$files = @('trading_corp/mace/config.py','trading_corp/mace/execution.py','trading_corp/mace/manager.py',
           'trading_corp/mace/loops.py','trading_corp/main.py')
if (Test-Path $tgz) { Remove-Item $tgz -Force }
tar.exe -czf $tgz -C $wt $files
scp -o BatchMode=yes -o ConnectTimeout=20 $tgz "${h}:/tmp/mace_durfix_graft.tgz"
$sh = @'
set -u
ROOT=/home/azureuser/trading_corp
STAGE=/tmp/mace_durfix_stage
V=$ROOT/venv/bin/python
TS=$(date -u +%Y%m%dT%H%M%SZ)
BK=/home/azureuser/mace_durfix_predeploy_backup_$TS
FILES="trading_corp/mace/config.py trading_corp/mace/execution.py trading_corp/mace/manager.py trading_corp/mace/loops.py trading_corp/main.py"
declare -A BASE TGT
BASE[trading_corp/mace/config.py]=d441957f;    TGT[trading_corp/mace/config.py]=4a47188b
BASE[trading_corp/mace/execution.py]=9cc1c165; TGT[trading_corp/mace/execution.py]=71f1a828
BASE[trading_corp/mace/manager.py]=dc04eb60;   TGT[trading_corp/mace/manager.py]=0116e860
BASE[trading_corp/mace/loops.py]=461867a8;     TGT[trading_corp/mace/loops.py]=765e3360
BASE[trading_corp/main.py]=b7cc5dd7;           TGT[trading_corp/main.py]=f486de42
cr(){ tr -d '\r' < "$1" | md5sum | cut -c1-8; }
rm -rf $STAGE; mkdir -p $STAGE
tar -xzf /tmp/mace_durfix_graft.tgz -C $STAGE
echo "=== DRIFT-GATE (box==base) + STAGE==target ==="
ABORT=0
for f in $FILES; do
  b=$(cr "$ROOT/$f"); s=$(cr "$STAGE/$f")
  printf "  %-34s box=%s base=%s | stage=%s target=%s\n" "$f" "$b" "${BASE[$f]}" "$s" "${TGT[$f]}"
  [ "$b" = "${BASE[$f]}" ] || { echo "  DRIFT: box != base for $f"; ABORT=1; }
  [ "$s" = "${TGT[$f]}" ] || { echo "  STAGE != target for $f"; ABORT=1; }
done
[ $ABORT -eq 0 ] || { echo "ABORT: drift/stage mismatch -- NOTHING applied"; rm -rf $STAGE /tmp/mace_durfix_graft.tgz; exit 2; }
echo "=== BACKUP live 5 -> $BK ==="
for f in $FILES; do mkdir -p "$BK/$(dirname $f)"; cp "$ROOT/$f" "$BK/$f"; done
echo "=== APPLY ==="
for f in $FILES; do cp "$STAGE/$f" "$ROOT/$f"; done
echo "=== VERIFY box==target ==="
VABORT=0
for f in $FILES; do b=$(cr "$ROOT/$f"); [ "$b" = "${TGT[$f]}" ] && echo "  OK   $f $b" || { echo "  FAIL $f box=$b != target=${TGT[$f]}"; VABORT=1; }; done
echo "=== py_compile ==="
$V -m py_compile $ROOT/trading_corp/mace/config.py $ROOT/trading_corp/mace/execution.py $ROOT/trading_corp/mace/manager.py $ROOT/trading_corp/mace/loops.py $ROOT/trading_corp/main.py && echo PYC_OK || VABORT=1
if [ $VABORT -ne 0 ]; then
  echo "*** FAILURE -> ROLLBACK from $BK ***"
  for f in $FILES; do cp "$BK/$f" "$ROOT/$f"; done
  echo "ROLLED BACK to base (box untouched vs pre-graft)."
  rm -rf $STAGE /tmp/mace_durfix_graft.tgz
  exit 4
fi
echo "=== config_hash unchanged? (yaml not grafted) ==="
$V -c "import hashlib;print('config_hash', hashlib.sha256(open('$ROOT/config/mace.yaml','rb').read()).hexdigest()[:12])"
echo "GRAFT_OK backup=$BK  (engine still runs OLD code until restart_tc.ps1)"
rm -rf $STAGE /tmp/mace_durfix_graft.tgz
echo DONE
'@
$sh | ssh -o BatchMode=yes -o ConnectTimeout=20 $h "tr -d '\r\357\273\277' | bash"
