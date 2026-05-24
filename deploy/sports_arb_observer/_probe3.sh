echo "=== grep 'online' in full journal since deploy ==="
journalctl -u trading-corp.service --since "2026-05-24 02:00:00" --no-pager | grep -i "online" | head -20
echo ""
echo "=== grep 'arb_observer' anywhere since deploy ==="
journalctl -u trading-corp.service --since "2026-05-24 02:00:00" --no-pager | grep -i "arb_observer\|Arb Observer\|sports_arb" | head -20
echo ""
echo "=== confirm new files installed ==="
ls -la /home/azureuser/trading_corp/trading_corp/agents/strategies/_sports_math.py /home/azureuser/trading_corp/trading_corp/agents/strategies/kalshi_sports_arb_observer.py 2>&1
echo ""
echo "=== confirm strategies.yaml has block + enabled true ==="
grep -A3 "kalshi_sports_arb_observer:" /home/azureuser/trading_corp/config/strategies.yaml | head -5
echo ""
echo "=== any errors at startup ==="
journalctl -u trading-corp.service --since "2026-05-24 02:00:00" --no-pager | grep -iE "Traceback|ImportError|ModuleNotFoundError|AttributeError|TypeError" | head -10
