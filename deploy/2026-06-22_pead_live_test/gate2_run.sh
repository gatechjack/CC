#!/usr/bin/env bash
# PEAD STEP 3 — GATE 2 routing proof (place-and-cancel, no fill).
# Run FROM the staged branch-checkout root on prod:
#     cd ~/pead_branch && bash deploy/2026-06-22_pead_live_test/gate2_run.sh
# Optional: PEAD_TEST_SYMBOL=F (default). Real non-marketable limit, cancelled
# immediately. Routes through the real engine path to 680725082; temp-DB record.
set -euo pipefail
export KEY_VAULT_URI="https://kv-tc-vtwbowt3wtkpy.vault.azure.net/"
export PYTHONPATH="$PWD"
export PYTHONIOENCODING="utf-8"
exec /home/azureuser/trading_corp/venv/bin/python \
    deploy/2026-06-22_pead_live_test/gate2_place_cancel.py
