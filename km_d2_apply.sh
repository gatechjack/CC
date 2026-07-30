true
R=/home/azureuser/trading_corp/trading_corp
CFG=/home/azureuser/trading_corp/config/strategies.yaml
BAK=bak_feedhealth_r1r2_20260730
KCT="$R/agents/strategies/kalshi_copy_trader.py"
MAIN="$R/main.py"
YAML="$CFG"
E_KCT=af336db8498c3543b8d824f471a43173
E_MAIN=6b6a03613f30443bdbf2edd1c5bcf5b4
E_YAML=3a8fb3db2557d73a192e178a34cb4480
B_KCT=720df3d8c5cadef044176566a09db3b9
B_MAIN=302c06e7776ae62fba576c8d66039ad1
B_YAML=6af510f67425a82f4208677a5c4558ef
md5of(){ tr -d '\r' < "$1" | md5sum | cut -d' ' -f1; }
fail=0
echo "=== STEP 1: decode /tmp b64 -> .new and verify == expected NEW md5 ==="
for pair in "kct:$E_KCT" "main:$E_MAIN" "yaml:$E_YAML"; do
  name=${pair%%:*}; exp=${pair##*:}
  tr -cd 'A-Za-z0-9+/=' < /tmp/r1r2_$name.b64 | base64 -d > /tmp/r1r2_$name.new 2>/dev/null || { echo "DECODE-FAIL $name"; fail=1; continue; }
  got=$(md5of /tmp/r1r2_$name.new)
  if [ "$got" = "$exp" ]; then echo "VERIFY-OK   $name $got"; else echo "VERIFY-FAIL $name got=$got exp=$exp"; fail=1; fi
done
if [ "$fail" != "0" ]; then echo "ABORT: decode/verify failed; NO live files touched."; echo "=== DONE d2 (ABORTED) ==="; exit 1; fi
echo "=== STEP 2: live files still == baseline (pre-backup drift-gate) ==="
for pair in "$KCT:$B_KCT" "$MAIN:$B_MAIN" "$YAML:$B_YAML"; do
  f=${pair%:*}; exp=${pair##*:}; got=$(md5of "$f")
  if [ "$got" = "$exp" ]; then echo "BASE-OK   $f"; else echo "BASE-DRIFT $f got=$got exp=$exp"; fail=1; fi
done
if [ "$fail" != "0" ]; then echo "ABORT: live drifted from baseline; NO changes."; echo "=== DONE d2 (ABORTED) ==="; exit 1; fi
echo "=== STEP 3: backup live files ==="
for f in "$KCT" "$MAIN" "$YAML"; do cp -p "$f" "$f.$BAK" && echo "BACKED-UP $f.$BAK"; done
echo "=== STEP 4: atomic apply (mv .new -> live) ==="
mv /tmp/r1r2_kct.new  "$KCT"  && echo "APPLIED $KCT"
mv /tmp/r1r2_main.new "$MAIN" && echo "APPLIED $MAIN"
mv /tmp/r1r2_yaml.new "$YAML" && echo "APPLIED $YAML"
echo "=== STEP 5: re-verify live == NEW md5 ==="
for pair in "$KCT:$E_KCT" "$MAIN:$E_MAIN" "$YAML:$E_YAML"; do
  f=${pair%:*}; exp=${pair##*:}; got=$(md5of "$f")
  if [ "$got" = "$exp" ]; then echo "LIVE-OK   $f $got"; else echo "LIVE-FAIL $f got=$got exp=$exp"; fi
done
echo "=== STEP 6: py_compile on prod venv (importability, python3.12) ==="
/home/azureuser/trading_corp/venv/bin/python -m py_compile "$KCT" "$MAIN" && echo "PYCOMPILE-OK" || echo "PYCOMPILE-FAIL"
echo "=== STEP 7: process NOT restarted (expect MainPID 450695, NRestarts 0) ==="
systemctl show trading-corp.service -p MainPID -p NRestarts 2>&1
echo "=== STEP 8: backups on disk ==="
ls -la "$KCT.$BAK" "$MAIN.$BAK" "$YAML.$BAK" 2>&1
echo "=== DONE d2 ==="
