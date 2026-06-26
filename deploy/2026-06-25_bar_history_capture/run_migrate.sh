#!/usr/bin/env bash
# Runbook step 2 wrapper: backup + migrate bitunix_bar_history (offline, engine stopped).
set -euo pipefail
cd ~/trading_corp
sqlite3 data/trading_corp.db < ~/capfix/migrate_bar_history.sql
