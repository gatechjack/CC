$ErrorActionPreference = 'Continue'
$h = 'azureuser@trading.jacksumner.com'
Write-Host '=== two-live Phase 1: DRIFT-GATE (read-only; abort apply on any mismatch) ==='
$cmd = @'
cd /home/azureuser/trading_corp || { echo PKG-MISSING; exit 9; }; ok=1; ck(){ m=$(md5sum "$1" 2>/dev/null | cut -d" " -f1); if [ "$m" = "$2" ]; then echo "  OK    $1"; else echo "  DRIFT $1 got=$m want=$2"; ok=0; fi; }; ck trading_corp/utils/secrets.py 385e9ded35ee92b05b43e06752053190; ck trading_corp/agents/divisions/bitunix_position_reconciler.py 3a23610c9e2bbd3d863163f657eeca36; ck trading_corp/main.py 2ff188c73648c2f23d92f1168a5a803f; if [ "$ok" = 1 ]; then echo "DRIFT-GATE: PASS (prod==BASE, safe to apply)"; else echo "DRIFT-GATE: FAIL (prod diverged from main - DO NOT APPLY; re-derive hunks)"; exit 2; fi
'@
$cmd | ssh $h "tr -d '\r'|bash"
Write-Host "ssh exit: $LASTEXITCODE  (0=PASS, 2=DRIFT abort)"
