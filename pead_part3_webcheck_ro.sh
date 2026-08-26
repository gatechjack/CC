echo "START webcheck"; date -u +%FT%TZ
echo "=== ALL listening TCP ports ==="
ss -ltn 2>/dev/null | awk 'NR>1{print $4}' | sort -u
echo "=== web/dashboard startup in boot journal (since restart) ==="
journalctl -u trading-corp --since '2026-08-26 18:26:00' -o cat 2>&1 | grep -Ei "uvicorn|Started server|Application startup|Uvicorn running|listen|bind|:8000|dashboard|web server|routes registered|healthz" | tail -20
echo "=== engine main PID + child procs (web thread?) ==="
ps -eo pid,ppid,cmd | grep -E 'trading_corp|pm_web|uvicorn' | grep -v grep | head
echo "=== curl probes ==="
for u in "http://127.0.0.1:8000/healthz" "http://127.0.0.1:8000/" "http://127.0.0.1:80/" "http://127.0.0.1:8080/healthz"; do
  echo -n "  $u -> "; curl -s -m 4 -o /dev/null -w "%{http_code}\n" "$u" 2>&1
done
echo "DONE webcheck"
