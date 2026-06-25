#!/usr/bin/env bash
# Flat-gated restart to load the new ExecStart. The unit edit (+robinhood_pead) is done
# separately via Azure Run Command (root). This script is NOPASSWD-only (systemctl) — no
# file edits, no sudo password. Run via: ssh azureuser@... 'bash /tmp/pead_golive/unit_flip_restart.sh'
set -uo pipefail
TC=/home/azureuser/trading_corp

echo "== confirm ExecStart already has robinhood_pead (Azure Run Command must have run) =="
es=$(systemctl show trading-corp -p ExecStart)
echo "  $(echo "$es" | grep -o 'live-divisions[^"]*' | head -1)"
echo "$es" | grep -q 'robinhood_pead' || { echo "ABORT: ExecStart lacks robinhood_pead — run the Azure Run Command edit first, then retry"; exit 8; }

echo "== confirm Bitunix FLAT (reconciler match_count==0) immediately before restart =="
mc=$($TC/venv/bin/python -c "import sqlite3,json;r=sqlite3.connect('$TC/data/trading_corp.db').execute(\"SELECT payload_json FROM audit_event WHERE kind='position_state_reconciled' ORDER BY ts DESC LIMIT 1\").fetchone();print(json.loads(r[0])['match_count'] if r else -1)")
echo "  match_count: $mc (0=flat)"
[ "$mc" = "0" ] || { echo "ABORT: Bitunix NOT flat ($mc) — wait for it to flatten, then retry. NOTHING restarted."; exit 9; }

echo "== daemon-reload + THE ONE RESTART (NOPASSWD systemctl) =="
sudo systemctl daemon-reload
sudo systemctl restart trading-corp && echo "  RESTARTED. Wait ~45s, then run golive_5_bootsmoke.ps1."
