#!/usr/bin/env bash
# PEAD STEP 3 — GATE 2 payload-first inspection (READ-ONLY, intercepts the POST).
# Run FROM the staged branch root on prod:
#     cd ~/pead_branch && bash deploy/2026-06-22_pead_live_test/payload_run.sh
set -euo pipefail
export KEY_VAULT_URI="https://kv-tc-vtwbowt3wtkpy.vault.azure.net/"
export PYTHONPATH="$PWD"
export PYTHONIOENCODING="utf-8"
exec /home/azureuser/trading_corp/venv/bin/python \
    deploy/2026-06-22_pead_live_test/gate2_payload.py
