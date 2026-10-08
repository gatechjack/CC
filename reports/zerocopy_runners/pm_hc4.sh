echo "=== shard_underfunded attribution (journal since arm) ==="
J=/tmp/hc4_$$.txt
journalctl -u trading-corp --since "2026-10-07 04:18:00" --no-pager -o short-iso 2>&1 | grep -iE "shard_underfunded|shard .* underfunded" > "$J" 2>&1
echo "total shard_underfunded lines:"; wc -l "$J"
echo "--- per account tally ---"
grep -oE "kalshi_(jack|karen|marc|trey)" "$J" | sort | uniq -c
echo "--- which shard underfunded (shard_N) ---"
grep -oE "shard_[0-9]+ underfunded|shard [0-9]+" "$J" | sort | uniq -c | head
echo "--- 4 sample lines ---"; head -4 "$J"
rm -f "$J"
echo "=== DONE hc4 ==="
