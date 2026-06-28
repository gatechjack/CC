#!/usr/bin/env bash
# bitunix_sfp Mode B — ARM GATE + flat-guarded restart (2026-06-28). Read-only
# flat check FIRST (positions + open live SFP rows); ABORT (no restart) if not
# flat. Else sudo -n systemctl restart (operator NOPASSWD). Run AFTER apply.
set -euo pipefail
DB=/home/azureuser/trading_corp/data/trading_corp.db
echo "== ARM GATE (read-only) =="
POS=$(sqlite3 -readonly "$DB" 'SELECT COUNT(*) FROM position;')
OPEN=$(sqlite3 -readonly "$DB" "SELECT COUNT(*) FROM paper_trade_record WHERE division='bitunix_sfp' AND result IS NULL;")
echo "  positions=$POS  open_bitunix_sfp_rows=$OPEN"
if [ "$POS" != "0" ] || [ "$OPEN" != "0" ]; then
  echo "ABORT: NOT FLAT (positions=$POS open_sfp=$OPEN) — NOT restarting."; exit 1
fi
echo "== flat — flat-guarded restart =="
sudo -n systemctl restart trading-corp
sleep 8
echo "== post-restart status =="
systemctl show trading-corp -p MainPID,ActiveState,SubState,NRestarts,ExecMainStartTimestamp
echo "RESTARTED — run the read-only boot smoke next."
