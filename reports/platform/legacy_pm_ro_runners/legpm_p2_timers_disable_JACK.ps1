$ErrorActionPreference = "Stop"
# PHASE 2 ITEM 2 -- stop AND disable the 4 confirmed-legacy timers (root, via az vm run-command).
# Prepared for Jack: passwordless sudo is unavailable on the box (NOPASSWD shadowed by trailing (ALL) ALL),
# so the agent could not do this over ssh. 'disable --now' stops + disables (reboot-persistent). Reversible
# via legpm_p2_timers_reenable_JACK.ps1. None of these serve the live PM division (verified Phase 2).
$rg = "rg-shared-prod"; $vm = "tc-prod-vm"
$script = "systemctl disable --now trading-corp-watchlist-stats.timer trading-corp-watchlist-deep.timer trading-corp-pm-watchlist-deep.timer trading-corp-pct-pruner.timer; echo '--- verify (expect: not in active list, is-enabled=disabled) ---'; systemctl list-timers --all --no-legend | grep -iE 'trading|watchlist|pct' ; echo '--- is-enabled ---'; systemctl is-enabled trading-corp-watchlist-stats.timer trading-corp-watchlist-deep.timer trading-corp-pm-watchlist-deep.timer trading-corp-pct-pruner.timer"
Write-Host "Disabling 4 legacy timers on $vm via az-root RunShellScript..."
az vm run-command invoke -g $rg -n $vm --command-id RunShellScript --scripts $script
