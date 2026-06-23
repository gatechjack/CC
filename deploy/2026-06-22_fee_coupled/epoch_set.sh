#!/usr/bin/env bash
# Set the bitunix_futures metrics_epoch agent_state row at the post-restart
# timestamp. Run AFTER the restart. Flips the dashboard amber "epoch not set"
# to "since <date>". Mirrors the existing polymarket_copy_trader/metrics_epoch
# shape: value_json is a JSON-quoted ISO-8601 UTC string. Idempotent (REPLACE).
set -euo pipefail
DB="${TC_DB:-/home/azureuser/trading_corp/data/trading_corp.db}"
[ -f "$DB" ] || { echo "[epoch] ABORT: missing $DB"; exit 2; }
TS=$(date -u +%Y-%m-%dT%H:%M:%S.%6N+00:00)
echo "[epoch] setting bitunix_futures/metrics_epoch = $TS"
sqlite3 "$DB" "INSERT OR REPLACE INTO agent_state(agent,key,value_json,updated_ts) VALUES('bitunix_futures','metrics_epoch','\"$TS\"','$TS');"
echo "[epoch] row now:"
sqlite3 -header -column "$DB" "SELECT agent,key,value_json,updated_ts FROM agent_state WHERE agent='bitunix_futures' AND key='metrics_epoch';"
echo "[epoch] DONE — dashboard should now read 'epoch since ${TS%%T*}'."
