$ErrorActionPreference = "Stop"
# PHASE 6 -- REMOVE the 4 legacy PM timer UNIT FILES (+ their 4 companion .service files) from the box.
# ROOT write via az vm run-command (the box NOPASSWD sudo is shadowed/dead -- ANOMALY-P2-1).
# These 4 timers were already stopped+disabled in Phase 2 (legpm_p2_timers_disable_JACK.ps1); this
# removes the unit files entirely. All 4 map to legacy PM scripts; none serves the live PM division.
# PRECONDITION-GATED (refuses if any is still active/enabled), BACKED UP (reversible), self-VERIFYING.
# Reserved for Jack. Does NOT touch survivors (pead-earnings-watcher / rh-relogin / tc-audit-reality)
# and does NOT restart trading-corp.
$rg = "rg-shared-prod"; $vm = "tc-prod-vm"
$script = @'
set -u
STEMS="trading-corp-watchlist-stats trading-corp-watchlist-deep trading-corp-pm-watchlist-deep trading-corp-pct-pruner"
echo "### PHASE-6 LEGACY TIMER UNIT REMOVAL (root) $(date -u +%FT%TZ) ###"
echo "=== PRECONDITION: all 4 must be disabled + inactive ==="
BAD=0
for s in $STEMS; do
  en=$(systemctl is-enabled "$s.timer" 2>/dev/null); ac=$(systemctl is-active "$s.timer" 2>/dev/null)
  echo "  $s.timer enabled=$en active=$ac"
  case "$en" in disabled|masked) ;; *) echo "   REFUSE: $s.timer not disabled"; BAD=1;; esac
  case "$ac" in inactive|failed) ;; *) echo "   REFUSE: $s.timer not inactive"; BAD=1;; esac
done
if [ "$BAD" != 0 ]; then echo "ABORT: preconditions not met -- run legpm_p2_timers_disable_JACK.ps1 first. NOTHING removed."; exit 3; fi
TS=$(date -u +%Y%m%dT%H%M%SZ); BK=/root/legpm_timer_unit_backup_$TS
mkdir -p "$BK"
echo "=== BACKUP 8 unit files -> $BK ==="
for s in $STEMS; do for e in timer service; do
  f=/etc/systemd/system/$s.$e
  if [ -f "$f" ]; then cp -p "$f" "$BK/" && echo "  backed up $f"; else echo "  (absent, skip) $f"; fi
done; done
echo "=== REMOVE 8 unit files ==="
for s in $STEMS; do for e in timer service; do
  f=/etc/systemd/system/$s.$e
  if [ -f "$f" ]; then rm -f "$f" && echo "  removed $f"; fi
done; done
systemctl daemon-reload
echo "=== VERIFY (checking STATE, not trusting return) ==="
for s in $STEMS; do
  echo "  $s.timer   is-enabled=$(systemctl is-enabled "$s.timer" 2>&1)   file_present=$([ -f /etc/systemd/system/$s.timer ] && echo YES || echo no)"
  echo "  $s.service is-enabled=$(systemctl is-enabled "$s.service" 2>&1) file_present=$([ -f /etc/systemd/system/$s.service ] && echo YES || echo no)"
done
echo "=== list-timers: target stems should be ABSENT ==="
systemctl list-timers --all --no-legend 2>/dev/null | grep -iE 'watchlist|pct-pruner' && echo "  !! STILL LISTED (unexpected)" || echo "  (none listed -- good)"
echo "=== SURVIVOR timers must REMAIN enabled+active ==="
for s in pead-earnings-watcher rh-relogin tc-audit-reality; do
  echo "  $s.timer enabled=$(systemctl is-enabled "$s.timer" 2>/dev/null) active=$(systemctl is-active "$s.timer" 2>/dev/null)"
done
echo "=== RESTORE (if ever needed, root): cp -p $BK/*.timer $BK/*.service /etc/systemd/system/ && systemctl daemon-reload && systemctl disable --now $STEMS ==="
echo "### DONE ###"
'@
Write-Host "Removing 4 legacy timer unit files (+companion services) on $vm via az-root RunShellScript..."
az vm run-command invoke -g $rg -n $vm --command-id RunShellScript --scripts $script
