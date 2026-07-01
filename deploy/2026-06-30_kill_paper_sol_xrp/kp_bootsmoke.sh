#!/usr/bin/env bash
# kill-paper-sol-xrp BOOTSMOKE (read-only). Run AFTER kp_restart. Confirms the
# engine came back, SFP wired to BTC+ETH ONLY, both reconcilers clean, no tracebacks.
echo "=== engine ==="
systemctl is-active trading-corp
echo -n "MainPID="; systemctl show trading-corp -p MainPID --value
echo -n "NRestarts="; systemctl show trading-corp -p NRestarts --value
echo "=== SFP wiring (symbols should be BTC+ETH only; symbol_modes 2 entries) ==="
sudo -n journalctl -u trading-corp --since '3 min ago' --no-pager | grep -iE "bitunix_sfp observer wired|bitunix_sfp mode gate" | tail -5
echo "=== boot-guard (2 live divisions, distinct refs) ==="
sudo -n journalctl -u trading-corp --since '3 min ago' --no-pager | grep -iE "boot-guard|live div|secret_ref" | tail -5
echo "=== reconcilers (both divisions) ==="
sudo -n journalctl -u trading-corp --since '3 min ago' --no-pager | grep -iE "position_state_reconciled|reconciler.*bitunix" | tail -6
echo "=== tracebacks / errors ==="
sudo -n journalctl -u trading-corp --since '3 min ago' --no-pager | grep -iE "Traceback|CRITICAL|observer wiring failed" | tail -10
echo "=== (bootsmoke done) ==="
