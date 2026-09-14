$ErrorActionPreference = "Stop"
# PHASE 6 -- REMOVE the 4 legacy PM timer unit files (+4 companion .service) from the box.
# Rebuild after the multi-line here-string variant returned empty output and did NOT run.
# SINGLE-LINE az --scripts (the proven pattern: disable_JACK / canonical restart_tc).
# Precondition-gated (aborts unless all 4 are disabled+inactive), backs up 8 files to
# /root/legpm_timer_unit_backup_<ts>, removes, daemon-reload, then re-reads actual state.
# Survivors (pead-earnings-watcher / rh-relogin / tc-audit-reality) untouched. Reserved for Jack.
$rg = "rg-shared-prod"; $vm = "tc-prod-vm"
$script = 'S="trading-corp-watchlist-stats trading-corp-watchlist-deep trading-corp-pm-watchlist-deep trading-corp-pct-pruner"; BAD=0; for s in $S; do en=$(systemctl is-enabled $s.timer 2>/dev/null); ac=$(systemctl is-active $s.timer 2>/dev/null); echo "PRE $s.timer enabled=$en active=$ac"; case "$en" in disabled|masked) ;; *) BAD=1;; esac; case "$ac" in inactive|failed) ;; *) BAD=1;; esac; done; if [ "$BAD" != 0 ]; then echo "ABORT: precondition not met (a timer is active/enabled) -- NOTHING removed"; else BK=/root/legpm_timer_unit_backup_$(date -u +%Y%m%dT%H%M%SZ); mkdir -p "$BK"; for s in $S; do for e in timer service; do f=/etc/systemd/system/$s.$e; if [ -f "$f" ]; then cp -p "$f" "$BK/" && rm -f "$f" && echo "removed $f"; fi; done; done; systemctl daemon-reload; echo "POST-STATE:"; for s in $S; do echo "  $s.timer enabled=$(systemctl is-enabled $s.timer 2>&1) present=$([ -f /etc/systemd/system/$s.timer ] && echo YES || echo no)"; done; echo "SURVIVORS:"; for s in pead-earnings-watcher rh-relogin tc-audit-reality; do echo "  $s.timer enabled=$(systemctl is-enabled $s.timer 2>/dev/null) active=$(systemctl is-active $s.timer 2>/dev/null)"; done; echo "BACKUP_DIR=$BK"; fi'
Write-Host "Removing 4 legacy timer unit files (+companions) on $vm via single-line az RunShellScript..."
az vm run-command invoke -g $rg -n $vm --command-id RunShellScript --scripts $script --query "value[0].message" -o tsv
