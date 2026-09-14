set -u
echo "### PHASE-6 TIMER-STATE CHECK (READ-ONLY) $(date -u +%FT%TZ) ###"
echo "=== target 8 unit files (PRESENT = not removed / ABSENT = removed) ==="
for stem in trading-corp-watchlist-stats trading-corp-watchlist-deep trading-corp-pm-watchlist-deep trading-corp-pct-pruner; do
  for e in timer service; do
    f=/etc/systemd/system/$stem.$e
    if [ -f "$f" ]; then echo "  PRESENT $f"; else echo "  ABSENT  $f"; fi
  done
done
echo "=== 4 legacy timers is-enabled / is-active (not-found = removed) ==="
for stem in trading-corp-watchlist-stats trading-corp-watchlist-deep trading-corp-pm-watchlist-deep trading-corp-pct-pruner; do
  echo "  $stem.timer enabled=$(systemctl is-enabled $stem.timer 2>&1) active=$(systemctl is-active $stem.timer 2>&1)"
done
echo "=== SURVIVOR timers (must remain enabled+active) ==="
for s in pead-earnings-watcher rh-relogin tc-audit-reality; do
  echo "  $s.timer enabled=$(systemctl is-enabled $s.timer 2>/dev/null) active=$(systemctl is-active $s.timer 2>/dev/null)"
done
echo "=== backup dir marker (root-only; azureuser cannot read /root, expected) ==="
ls -d /root/legpm_timer_unit_backup_* 2>/dev/null || echo "  (cannot stat /root backups as azureuser -- expected; not diagnostic)"
echo "=== engine unaffected (PID should still be 397094 / boot 00:25:13) ==="
systemctl show trading-corp -p MainPID,ActiveState,SubState,ExecMainStartTimestamp 2>/dev/null
echo "### DONE ###"
