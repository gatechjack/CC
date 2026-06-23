#!/usr/bin/env bash
# PEAD STEP 3 — GATE 4 round-trip EXIT (deliberate stop via real manage()).
#     cd ~/pead_branch && bash deploy/2026-06-22_pead_live_test/exit_run.sh
# Env: PEAD_RT_SYMBOL (default F) — must match the entry.
set -euo pipefail
export KEY_VAULT_URI="https://kv-tc-vtwbowt3wtkpy.vault.azure.net/"
export PYTHONPATH="$PWD"
export PYTHONIOENCODING="utf-8"
export PEAD_DB_URL="sqlite:////home/azureuser/trading_corp/data/trading_corp.db"
exec /home/azureuser/trading_corp/venv/bin/python deploy/2026-06-22_pead_live_test/gate34_roundtrip.py exit
