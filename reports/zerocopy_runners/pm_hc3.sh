J=/tmp/hcj_$$.txt
echo "=== restarts today + boot-reconcile result (2026-10-07 00:00 onward) ==="
journalctl -u trading-corp --since "2026-10-07 00:00:00" --no-pager -o short-iso 2>&1 | grep -iE "MACE wired|boot-reconcile account=" | head -30
echo "=== single-scan grep of the day for 429 / REVIEW / WARNING / guards (since arm) ==="
journalctl -u trading-corp --since "2026-10-07 04:18:00" --no-pager -o short-iso 2>&1 | grep -iE "HTTP 429|REVIEW|WARNING|abbrev_collision|winner_outcome_unresolved|driver_ambiguous|OPPOSING-PAIR|whale-EXIT|shard_underfunded|shard_read_failed|exposure_unknown|Traceback|CRITICAL|code_review|inversion" > "$J" 2>&1
echo "matched lines total:"; wc -l "$J"
echo "--- HTTP 429 count today + first + last ---"
echo -n "count: "; grep -c "HTTP 429" "$J"
grep "HTTP 429" "$J" | head -1; echo "..."; grep "HTTP 429" "$J" | tail -1
echo "--- 429 per hour ---"; grep "HTTP 429" "$J" | awk '{print substr($1,1,13)}' | uniq -c
echo "--- guards (counts) ---"
for g in "OPPOSING-PAIR" "whale-EXIT" "shard_underfunded" "shard_read_failed" "exposure_unknown" "abbrev_collision" "winner_outcome_unresolved" "driver_ambiguous"; do
  echo -n "  $g: "; grep -c "$g" "$J"
done
echo "--- REVIEW / inversion / code_review lines (verbatim, non-429) ---"
grep -iE "REVIEW|inversion|code_review" "$J" | grep -viE "HTTP 429" | head -20 || echo "(none)"
echo "--- WARNING / Traceback / CRITICAL (non-429, sample) ---"
grep -iE "WARNING|Traceback|CRITICAL" "$J" | grep -viE "HTTP 429|positions fetch failed" | head -20 || echo "(none beyond 429 fetch-fails)"
echo "--- WARNING types tally (non-429) ---"
grep -iE "WARNING" "$J" | grep -viE "HTTP 429" | sed -E 's/.*WARNING //' | cut -c1-60 | sort | uniq -c | sort -rn | head -15
rm -f "$J"
echo "=== DONE hc3 ==="
