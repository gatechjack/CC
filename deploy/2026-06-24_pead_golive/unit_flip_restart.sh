#!/usr/bin/env bash
# Go-live flip 2 (ExecStart +robinhood_pead) + the ONE restart. Uses sudo.
# Run via:  ssh -t azureuser@... 'bash /tmp/pead_golive/unit_flip_restart.sh'
set -uo pipefail
U=/etc/systemd/system/trading-corp.service
TC=/home/azureuser/trading_corp

echo "== re-confirm Bitunix FLAT (reconciler match_count==0) immediately before restart =="
mc=$($TC/venv/bin/python -c "import sqlite3,json;c=sqlite3.connect('$TC/data/trading_corp.db');r=c.execute(\"SELECT payload_json FROM audit_event WHERE kind='position_state_reconciled' ORDER BY ts DESC LIMIT 1\").fetchone();print(json.loads(r[0])['match_count'] if r else -1)")
echo "  reconciler OPEN match_count: $mc  (0 = flat)"
[ "$mc" = "0" ] || { echo "ABORT: Bitunix NOT flat (match_count=$mc) — wait for it to flatten, then re-run. NOTHING restarted."; exit 9; }

echo "== backup unit + flip ExecStart (idempotent) =="
sudo cp -p "$U" "$U.bak-pre-golive-2026-06-24"
if grep -q 'live-divisions bitunix_futures robinhood_pead' "$U"; then
  echo "  ExecStart already includes robinhood_pead (idempotent no-op)"
else
  sudo sed -i 's/--live-divisions bitunix_futures/--live-divisions bitunix_futures robinhood_pead/' "$U"
fi
grep -q 'live-divisions bitunix_futures robinhood_pead' "$U" || { echo "ABORT: ExecStart edit failed"; exit 8; }
sudo systemctl daemon-reload
echo "  ExecStart now: $(grep -o 'live-divisions bitunix_futures\( robinhood_pead\)\?' "$U" | head -1)"

echo "== THE ONE RESTART =="
sudo systemctl restart trading-corp && echo "  RESTARTED. Wait ~45s, then run golive_5_bootsmoke.ps1."
