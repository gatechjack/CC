$ErrorActionPreference = "Stop"
# ROLLBACK for legpm_p2_timers_disable_JACK.ps1 -- re-enable + start the 4 legacy timers (root, az-root).
$rg = "rg-shared-prod"; $vm = "tc-prod-vm"
$script = "systemctl enable --now trading-corp-watchlist-stats.timer trading-corp-watchlist-deep.timer trading-corp-pm-watchlist-deep.timer trading-corp-pct-pruner.timer; echo '--- verify active list ---'; systemctl list-timers --all --no-legend | grep -iE 'trading|watchlist|pct'"
Write-Host "Re-enabling 4 legacy timers on $vm via az-root RunShellScript..."
az vm run-command invoke -g $rg -n $vm --command-id RunShellScript --scripts $script
