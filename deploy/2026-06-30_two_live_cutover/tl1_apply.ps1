$ErrorActionPreference = 'Continue'
$h = 'azureuser@trading.jacksumner.com'
Write-Host '=== two-live Phase 1: APPLY (re-drift-gate + backup + patch + verify TARGET + py_compile; NO restart) ==='
Write-Host '--- scp patch to prod home ---'
scp ".\phase1_code.patch" "${h}:phase1_code.patch"
Write-Host "scp exit: $LASTEXITCODE"
$cmd = @'
cd /home/azureuser/trading_corp || { echo PKG-MISSING; exit 9; }; F="trading_corp/utils/secrets.py trading_corp/agents/divisions/bitunix_position_reconciler.py trading_corp/main.py"; ok=1; ck(){ m=$(md5sum "$1" 2>/dev/null | cut -d" " -f1); [ "$m" = "$2" ] && echo "  base-OK    $1" || { echo "  base-DRIFT $1 got=$m want=$2"; ok=0; }; }; ck trading_corp/utils/secrets.py 385e9ded35ee92b05b43e06752053190; ck trading_corp/agents/divisions/bitunix_position_reconciler.py 3a23610c9e2bbd3d863163f657eeca36; ck trading_corp/main.py 2ff188c73648c2f23d92f1168a5a803f; [ "$ok" = 1 ] || { echo "DRIFT - ABORT (no changes made)"; exit 2; }; for f in $F; do cp "$f" "$f.bak-pre-two-live-2026-06-29"; done; echo "backed up (.bak-pre-two-live-2026-06-29)"; restore(){ for f in $F; do cp "$f.bak-pre-two-live-2026-06-29" "$f"; done; echo RESTORED; }; tr -d "\r" < ~/phase1_code.patch | patch -p1 || { echo "PATCH FAILED"; restore; exit 3; }; vok=1; vk(){ m=$(md5sum "$1" | cut -d" " -f1); [ "$m" = "$2" ] && echo "  target-OK  $1" || { echo "  target-BAD $1 got=$m want=$2"; vok=0; }; }; vk trading_corp/utils/secrets.py 6230e35138b9c11a01318b986ed52c7f; vk trading_corp/agents/divisions/bitunix_position_reconciler.py 68f969d6f66a1953a7b975e670436de9; vk trading_corp/main.py f4f0880d6062e6de04925b06e6c6366e; [ "$vok" = 1 ] || { echo "TARGET MD5 MISMATCH"; restore; exit 5; }; python3 -m py_compile $F && echo "PY_COMPILE OK" || { echo "PY_COMPILE FAILED"; restore; exit 4; }; echo "APPLY COMPLETE - new code on disk; takes effect on next engine start (your hard reboot, or tl1_restart)"
'@
$cmd | ssh $h "tr -d '\r'|bash"
Write-Host "ssh exit: $LASTEXITCODE  (0=applied+verified; 2=drift,3=patch,4=compile,5=target - all auto-restore)"
