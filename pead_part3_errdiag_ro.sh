echo "START errdiag"; date -u +%FT%TZ
echo "=== error-line CATEGORIES since restart (18:26) ==="
J=$(journalctl -u trading-corp --since '2026-08-26 18:26:00' -o cat 2>&1)
printf '%s\n' "$J" | grep -Ei 'Traceback|FATAL|CRITICAL|unhandled|Error' > /tmp/errs.txt
echo "  total_error_lines=$(wc -l < /tmp/errs.txt)"
echo "  mention_pead=$(grep -ci 'pead' /tmp/errs.txt)"
echo "  mention_robinhood=$(grep -ci 'robinhood' /tmp/errs.txt)"
echo "  mention_QuoteSymbolUnresolved=$(grep -ci 'QuoteSymbolUnresolved' /tmp/errs.txt)"
echo "  mention_ImportError/NameError=$(grep -ciE 'ImportError|NameError|ModuleNotFound|AttributeError' /tmp/errs.txt)"
echo "  mention_poly_kalshi=$(grep -ci 'poly_kalshi\|403\|geo' /tmp/errs.txt)"
echo "  mention_kalshi=$(grep -ci 'kalshi' /tmp/errs.txt)"
echo "  mention_bitunix=$(grep -ci 'bitunix' /tmp/errs.txt)"
echo "=== last 30 error lines (verbatim) ==="
tail -30 /tmp/errs.txt
echo "=== any Traceback blocks (3 lines context) ==="
printf '%s\n' "$J" | grep -A3 -i 'Traceback' | tail -30
echo "=== web server / healthz ==="
ss -ltn 2>/dev/null | grep -E ':8000|:8050|:80 ' | head
echo -n "curl / : "; curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/ 2>&1
echo -n "curl /healthz : "; curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/healthz 2>&1
echo -n "curl /telemetry/pead : "; curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/telemetry/pead 2>&1
echo "DONE errdiag"
