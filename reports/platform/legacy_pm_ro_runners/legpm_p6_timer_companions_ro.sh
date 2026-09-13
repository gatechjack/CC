set -u
echo "### PHASE-6 TIMER COMPANION DISCOVERY (READ-ONLY) $(date -u +%FT%TZ) ###"
cd /etc/systemd/system 2>/dev/null || { echo "cd fail"; exit 2; }
for t in trading-corp-watchlist-stats trading-corp-watchlist-deep trading-corp-pm-watchlist-deep trading-corp-pct-pruner; do
  echo "=== $t ==="
  echo "-- timer file present: $(ls -la $t.timer 2>/dev/null || echo MISSING) --"
  echo "-- [Timer] Unit= (triggered target) --"; grep -iE '^\s*Unit=' "$t.timer" 2>/dev/null || echo "  (no explicit Unit= -> triggers ${t}.service by convention)"
  echo "-- companion service file: $(ls -la $t.service 2>/dev/null || echo 'no same-stem .service') --"
  echo "-- service ExecStart --"; grep -iE '^\s*ExecStart=' "$t.service" 2>/dev/null || echo "  (no .service or no ExecStart)"
done
echo "=== cross-reference: any OTHER unit that Requires/Wants/references these? ==="
grep -rilE 'trading-corp-(watchlist-stats|watchlist-deep|pm-watchlist-deep|pct-pruner)' /etc/systemd/system 2>/dev/null | grep -vE '\.(timer|service)$|trading-corp-(watchlist-stats|watchlist-deep|pm-watchlist-deep|pct-pruner)\.' || echo "(no external references)"
echo "=== full list of the 8 target files (4 .timer + 4 .service) ==="
for stem in trading-corp-watchlist-stats trading-corp-watchlist-deep trading-corp-pm-watchlist-deep trading-corp-pct-pruner; do
  for ext in timer service; do
    f="$stem.$ext"; [ -f "$f" ] && printf "  PRESENT  %s  (md5 %s)\n" "$f" "$(md5sum "$f" | cut -c1-32)" || printf "  ABSENT   %s\n" "$f"
  done
done
echo "### DONE ###"
