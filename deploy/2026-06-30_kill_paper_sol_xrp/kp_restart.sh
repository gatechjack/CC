#!/usr/bin/env bash
# kill-paper-sol-xrp RESTART (prod). Flat-guard (plain sqlite3 - DB is azureuser-
# owned; sudo -n sqlite3 is NOT NOPASSWD here) then sudo -n systemctl restart
# (systemctl IS NOPASSWD). ABORTS with NO restart if any open LIVE bitunix_sfp row.
DB=/home/azureuser/trading_corp/data/trading_corp.db
echo "=== FLAT-GUARD: open LIVE bitunix_sfp rows (result IS NULL AND execution_mode=live) ==="
Q="SELECT COUNT(*) FROM paper_trade_record WHERE division='bitunix_sfp' AND result IS NULL AND execution_mode='live'"
OPEN=$(sqlite3 "$DB" "$Q" 2>&1)
if [ "$OPEN" != "0" ]; then
  echo "NOT FLAT or check failed (got: [$OPEN]) - ABORT, NO restart"; exit 3
fi
echo "SFP FLAT (0 open live rows) - restarting trading-corp"
sudo -n systemctl restart trading-corp
sleep 4
echo "RESTART ISSUED - is-active: $(systemctl is-active trading-corp)  PID: $(systemctl show trading-corp -p MainPID --value)"
