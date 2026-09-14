$ErrorActionPreference = "Stop"
# PHASE 6 -- REMOVE 4 legacy PM timer unit files (+4 companion .service). remove3: SIMPLE single-line
# az --scripts with NO embedded double-quotes (paths have no spaces), timestamp baked from PowerShell.
# Precondition (all 4 disabled+inactive) was RO-verified via legpm_p6_timerstate_ro immediately before;
# removing a disabled+inactive unit's files is safe. Verify AFTER via legpm_p6_timerstate_ro (ssh-STDIN).
# Backs up all 8 files to /root/legpm_timer_unit_backup_<ts> (cp && rm: rm only runs if cp succeeds).
# Reserved for Jack (az-root write). Survivors pead/rh-relogin/audit-reality are never touched.
$rg = "rg-shared-prod"; $vm = "tc-prod-vm"
$ts = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$bk = "/root/legpm_timer_unit_backup_$ts"
$stems = @("trading-corp-watchlist-stats","trading-corp-watchlist-deep","trading-corp-pm-watchlist-deep","trading-corp-pct-pruner")
$files = (($stems | ForEach-Object { "/etc/systemd/system/$_.timer /etc/systemd/system/$_.service" }) -join " ")
$script = "mkdir -p $bk && cp -p $files $bk/ && rm -f $files && systemctl daemon-reload && echo TIMERS_REMOVED_OK backup=$bk"
Write-Host "Removing 4 legacy timer unit files (+companions) on $vm via SIMPLE single-line az..."
Write-Host "  az script: $script"
az vm run-command invoke -g $rg -n $vm --command-id RunShellScript --scripts $script --query "value[0].message" -o tsv
