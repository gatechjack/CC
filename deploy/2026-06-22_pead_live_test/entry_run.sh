#!/usr/bin/env bash
# PEAD STEP 3 — GATE 3 round-trip ENTRY (real marketable buy). Run AT THE OPEN.
#     cd ~/pead_branch && bash deploy/2026-06-22_pead_live_test/entry_run.sh
# Env: PEAD_RT_SYMBOL (default F), PEAD_RT_QTY (default 1).
set -euo pipefail
export KEY_VAULT_URI="https://kv-tc-vtwbowt3wtkpy.vault.azure.net/"
export PYTHONPATH="$PWD"
export PYTHONIOENCODING="utf-8"
exec /home/azureuser/trading_corp/venv/bin/python deploy/2026-06-22_pead_live_test/gate34_roundtrip.py entry
