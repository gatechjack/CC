$ErrorActionPreference = 'Continue'
$h = 'azureuser@trading.jacksumner.com'
Write-Host '=== two-live Phase 1: BOOT-SMOKE (read-only; run AFTER restart/hard-reboot) ==='
$cmd = @'
cd /home/azureuser/trading_corp || exit 9; echo "-- ACTIVE / PID / restarts --"; systemctl is-active trading-corp; systemctl show -p MainPID,NRestarts,ActiveEnterTimestamp trading-corp; J=$(sudo -n journalctl -u trading-corp -n 1500 --no-pager 2>/dev/null); echo "-- boot-guard (want: none) --"; echo "$J" | grep -i "REFUSING TO START" || echo "  (none - boot-guard PASSED)"; echo "-- SFP mode gate + reconciler[bitunix_sfp] (legacy division=None path) --"; echo "$J" | grep -E "bitunix_sfp mode gate|restart-resume \[bitunix_sfp\]|reconciler \[bitunix_sfp\] at startup" | tail -6; echo "-- futures inert (want HALTED + execution_mode=paper) --"; echo "$J" | grep -E "bitunix_futures HALTED|execution_mode=paper" | tail -3; echo "-- KV bitunix-sfp (want loaded, no error) --"; echo "$J" | grep -iE "Key Vault: loaded|bitunix-sfp" | tail -4; echo "-- SFP files md5 (want 8a916526.. / 91fd7672..) --"; md5sum trading_corp/agents/divisions/bitunix_sfp_observer.py trading_corp/agents/strategies/bitunix_sfp.py; echo "-- tracebacks since boot (want 0) --"; echo "$J" | grep -c "Traceback"
'@
$cmd | ssh $h "tr -d '\r'|bash"
Write-Host "ssh exit: $LASTEXITCODE"
