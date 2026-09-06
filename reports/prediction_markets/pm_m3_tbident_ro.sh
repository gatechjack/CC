set -u
ST=$(systemctl show -p ActiveEnterTimestamp --value trading-corp 2>/dev/null | sed 's/^[A-Za-z]* //')
echo "### IDENTIFY THE TypeError SOURCE (READ-ONLY) since=$ST ###"
echo "## modules in the traceback frames (File paths, ranked) -- names the culprit: ##"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -oE 'File "[^"]+", line [0-9]+, in [A-Za-z_]+' | sed -E 's/, line [0-9]+//' | sort | uniq -c | sort -rn | head -15
echo
echo "## loggers that emit the bad format (line before 'not all arguments converted'): ##"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -B3 'not all arguments converted' | grep -oE 'trading_corp[A-Za-z_.]+|mace[A-Za-z_.]*|dxlink[A-Za-z_.]*|[A-Za-z_]+_feed' | sort | uniq -c | sort -rn | head
echo
echo "## one COMPLETE traceback (frames + final line), PM-relevance check: ##"
journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | awk '/Traceback \(most recent call last\)/{f=1} f{print} /not all arguments converted/{if(f){exit}}' | sed -E 's/.*xvfb-run\[[0-9]+\]: //' | sed -E 's/(.{170}).*/\1/' | head -24
echo
echo "## does 'prediction_markets' or 'shard_snapshot' appear in ANY of these tracebacks? (expect 0) ##"
echo "  pm/shard frames = $(journalctl -u trading-corp --since "$ST" --no-pager 2>/dev/null | grep -A20 'Traceback' | grep -icE 'prediction_markets|shard_snapshot')"
echo "### DONE ###"
